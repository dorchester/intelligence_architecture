"""Dataset query layer.

Reads synthetic LinkedIn profiles and job postings from S3 and computes
workforce intelligence signals the agent can reason over.

Company isolation is structural: every query requires a company_id and
S3 keys are built from it. A query for company A cannot return company B's
records because the key prefix physically differs.

Uses S3 Select where beneficial, falls back to full-object reads for
smaller datasets (our per-company files are a few MB at most).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

import boto3


@dataclass
class DatasetSummary:
    """Computed workforce intelligence for one company."""

    company_id: str
    company_name: str
    industry: str
    headcount: int

    # Profile-derived signals
    profiles_analyzed: int = 0
    department_distribution: dict[str, int] = field(default_factory=dict)
    seniority_distribution: dict[str, int] = field(default_factory=dict)
    location_distribution: dict[str, int] = field(default_factory=dict)
    avg_tenure_years: float = 0.0
    median_tenure_years: float = 0.0
    tenure_by_department: dict[str, float] = field(default_factory=dict)
    top_skills: list[tuple[str, int]] = field(default_factory=list)
    top_universities: list[tuple[str, int]] = field(default_factory=list)
    flight_risk_pct: float = 0.0
    flight_risk_by_department: dict[str, float] = field(default_factory=dict)
    open_to_opportunities_pct: float = 0.0
    external_hire_ratio: float = 0.0
    avg_prior_employers: float = 0.0

    # Posting-derived signals
    postings_analyzed: int = 0
    postings_by_department: dict[str, int] = field(default_factory=dict)
    postings_by_quarter: dict[str, int] = field(default_factory=dict)
    active_postings: int = 0
    avg_days_to_fill: float = 0.0
    days_to_fill_by_department: dict[str, float] = field(default_factory=dict)
    remote_pct: float = 0.0
    work_arrangement_mix: dict[str, int] = field(default_factory=dict)
    seniority_demand: dict[str, int] = field(default_factory=dict)
    avg_salary_by_seniority: dict[str, int] = field(default_factory=dict)
    hardest_to_fill: list[dict] = field(default_factory=list)
    top_demanded_skills: list[tuple[str, int]] = field(default_factory=list)
    hiring_velocity_trend: str = ""

    # Social signals
    profiles_with_activity: int = 0
    avg_posts_per_active_profile: float = 0.0
    top_discussion_topics: list[tuple[str, int]] = field(default_factory=list)
    job_change_signal_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_agent_context(self) -> str:
        """Compact text rendering for LLM prompts."""
        lines = [
            f"WORKFORCE DATASET — {self.company_name}",
            f"Industry: {self.industry} | Reported headcount: {self.headcount:,}",
            f"Sample: {self.profiles_analyzed:,} employee profiles, "
            f"{self.postings_analyzed:,} job postings (24 months)",
            "",
            "WORKFORCE COMPOSITION",
        ]
        for dept, count in sorted(
            self.department_distribution.items(), key=lambda x: -x[1]
        )[:10]:
            pct = count / max(self.profiles_analyzed, 1) * 100
            tenure = self.tenure_by_department.get(dept, 0)
            risk = self.flight_risk_by_department.get(dept, 0)
            lines.append(
                f"  {dept}: {count} ({pct:.1f}%) | "
                f"avg tenure {tenure:.1f}y | flight risk {risk:.0f}%"
            )

        lines += [
            "",
            "SENIORITY MIX",
            "  " + ", ".join(
                f"{k}: {v}" for k, v in sorted(
                    self.seniority_distribution.items(), key=lambda x: -x[1]
                )
            ),
            "",
            "TENURE & RETENTION",
            f"  Average tenure: {self.avg_tenure_years:.1f} years",
            f"  Median tenure: {self.median_tenure_years:.1f} years",
            f"  Flight risk (elevated): {self.flight_risk_pct:.1f}% of workforce",
            f"  Signalling openness to opportunities: {self.open_to_opportunities_pct:.1f}%",
            f"  External hires vs internal promotions: {self.external_hire_ratio:.0%} external",
            f"  Average prior employers per person: {self.avg_prior_employers:.1f}",
            "",
            "HIRING ACTIVITY (24 months)",
            f"  Total postings: {self.postings_analyzed:,}",
            f"  Currently active: {self.active_postings}",
            f"  Average days to fill: {self.avg_days_to_fill:.0f}",
            f"  Remote-eligible: {self.remote_pct:.0f}%",
            f"  Trend: {self.hiring_velocity_trend}",
            "",
            "HIRING BY FUNCTION",
        ]
        for dept, count in sorted(
            self.postings_by_department.items(), key=lambda x: -x[1]
        )[:8]:
            dtf = self.days_to_fill_by_department.get(dept, 0)
            lines.append(f"  {dept}: {count} postings | {dtf:.0f} days avg to fill")

        if self.hardest_to_fill:
            lines += ["", "HARDEST ROLES TO FILL"]
            for role in self.hardest_to_fill[:5]:
                lines.append(
                    f"  {role['title']} ({role['department']}): "
                    f"{role['avg_days_to_fill']:.0f} days, {role['count']} postings"
                )

        lines += [
            "",
            "COMPENSATION BANDS (posted midpoint)",
            "  " + ", ".join(
                f"{k}: ${v:,}" for k, v in self.avg_salary_by_seniority.items()
            ),
            "",
            "IN-DEMAND SKILLS",
            "  " + ", ".join(f"{s} ({c})" for s, c in self.top_demanded_skills[:10]),
            "",
            "WORKFORCE SKILLS ON HAND",
            "  " + ", ".join(f"{s} ({c})" for s, c in self.top_skills[:10]),
            "",
            "SOCIAL SIGNALS",
            f"  Profiles with recent activity: {self.profiles_with_activity} "
            f"({self.profiles_with_activity / max(self.profiles_analyzed, 1) * 100:.0f}%)",
            f"  Job-change posts detected: {self.job_change_signal_count}",
        ]
        if self.top_discussion_topics:
            lines.append("  Common topics: " + ", ".join(
                f"{t}" for t, _ in self.top_discussion_topics[:5]
            ))

        return "\n".join(lines)


class DatasetQuery:
    """Query synthetic workforce datasets stored in S3."""

    PREFIX = "datasets"

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        profile: str | None = None,
        max_profiles: int = 1000,
        max_postings: int = 1000,
    ):
        self.bucket = bucket
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs, region_name=region)
        self.s3 = session.client("s3")
        self.max_profiles = max_profiles
        self.max_postings = max_postings
        self._cache: dict[str, Any] = {}

    # ----- discovery -----

    def list_companies(self) -> list[dict]:
        """Return the dataset manifest (all available companies)."""
        if "manifest" in self._cache:
            return self._cache["manifest"]
        try:
            obj = self.s3.get_object(
                Bucket=self.bucket, Key=f"{self.PREFIX}/manifest.json"
            )
            manifest = json.loads(obj["Body"].read())
            self._cache["manifest"] = manifest
            return manifest
        except Exception:
            return []

    def has_dataset(self, company_id: str) -> bool:
        try:
            self.s3.head_object(
                Bucket=self.bucket,
                Key=f"{self.PREFIX}/{company_id}/metadata.json",
            )
            return True
        except Exception:
            return False

    def get_metadata(self, company_id: str) -> dict | None:
        try:
            obj = self.s3.get_object(
                Bucket=self.bucket,
                Key=f"{self.PREFIX}/{company_id}/metadata.json",
            )
            return json.loads(obj["Body"].read())
        except Exception:
            return None

    # ----- raw record access -----

    def _read_jsonl(self, company_id: str, kind: str, limit: int) -> list[dict]:
        """Read a JSONL file from the company's prefix.

        Key is constructed from company_id, so cross-company reads are
        impossible through this interface.
        """
        cache_key = f"{company_id}:{kind}"
        if cache_key in self._cache:
            return self._cache[cache_key][:limit]

        key = f"{self.PREFIX}/{company_id}/{kind}.jsonl"
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
            body = obj["Body"].read().decode("utf-8")
        except Exception:
            return []

        records = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        self._cache[cache_key] = records
        return records[:limit]

    def get_profiles(self, company_id: str, limit: int | None = None) -> list[dict]:
        return self._read_jsonl(
            company_id, "profiles", limit or self.max_profiles
        )

    def get_postings(self, company_id: str, limit: int | None = None) -> list[dict]:
        return self._read_jsonl(
            company_id, "postings", limit or self.max_postings
        )

    # ----- filtered queries -----

    def query_profiles(
        self,
        company_id: str,
        department: str | None = None,
        seniority: str | None = None,
        min_tenure: float | None = None,
        max_tenure: float | None = None,
        flight_risk_above: float | None = None,
        skill: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Filtered profile query, always scoped to one company."""
        profiles = self.get_profiles(company_id, limit=self.max_profiles)
        out = []
        for p in profiles:
            if department and p.get("department") != department:
                continue
            if seniority and p.get("seniority_level") != seniority:
                continue
            tenure = p.get("tenure_years", 0)
            if min_tenure is not None and tenure < min_tenure:
                continue
            if max_tenure is not None and tenure > max_tenure:
                continue
            if flight_risk_above is not None and p.get("turnover_risk_score", 0) < flight_risk_above:
                continue
            if skill and skill.lower() not in [s.lower() for s in p.get("skills", [])]:
                continue
            out.append(p)
            if limit and len(out) >= limit:
                break
        return out

    def query_postings(
        self,
        company_id: str,
        department: str | None = None,
        status: str | None = None,
        seniority: str | None = None,
        since: str | None = None,
        remote_only: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        """Filtered posting query, always scoped to one company."""
        postings = self.get_postings(company_id, limit=self.max_postings)
        out = []
        for p in postings:
            if department and p.get("department") != department:
                continue
            if status and p.get("status") != status:
                continue
            if seniority and p.get("seniority_level") != seniority:
                continue
            if remote_only and p.get("work_arrangement") != "Remote":
                continue
            if since and p.get("posted_date", "") < since:
                continue
            out.append(p)
            if limit and len(out) >= limit:
                break
        return out

    # ----- analysis -----

    def summarize(self, company_id: str) -> DatasetSummary | None:
        """Compute the full workforce intelligence summary for a company."""
        meta = self.get_metadata(company_id)
        if not meta:
            return None

        profiles = self.get_profiles(company_id)
        postings = self.get_postings(company_id)

        s = DatasetSummary(
            company_id=company_id,
            company_name=meta.get("company_name", company_id),
            industry=meta.get("industry", ""),
            headcount=meta.get("headcount", 0),
        )

        if profiles:
            self._analyze_profiles(s, profiles)
        if postings:
            self._analyze_postings(s, postings)

        return s

    def _analyze_profiles(self, s: DatasetSummary, profiles: list[dict]) -> None:
        s.profiles_analyzed = len(profiles)

        dept_counter = Counter()
        seniority_counter = Counter()
        location_counter = Counter()
        skill_counter = Counter()
        university_counter = Counter()
        topic_counter = Counter()

        tenures: list[float] = []
        tenure_by_dept: dict[str, list[float]] = defaultdict(list)
        risk_by_dept: dict[str, list[float]] = defaultdict(list)

        elevated_risk = 0
        open_to_opps = 0
        external_moves = 0
        internal_moves = 0
        prior_employer_counts: list[int] = []
        with_activity = 0
        total_posts = 0
        job_change_posts = 0

        for p in profiles:
            dept = p.get("department", "Unknown")
            dept_counter[dept] += 1
            seniority_counter[p.get("seniority_level", "Unknown")] += 1
            location_counter[p.get("location", "Unknown")] += 1

            tenure = float(p.get("tenure_years", 0) or 0)
            tenures.append(tenure)
            tenure_by_dept[dept].append(tenure)

            risk = float(p.get("turnover_risk_score", 0) or 0)
            risk_by_dept[dept].append(risk)
            if risk >= 0.3:
                elevated_risk += 1
            if p.get("open_to_opportunities"):
                open_to_opps += 1

            for skill in p.get("skills", []):
                skill_counter[skill] += 1

            edu = p.get("education") or {}
            if edu.get("university"):
                university_counter[edu["university"]] += 1

            history = p.get("job_history", []) or []
            company_name = p.get("company")
            prior = [h for h in history if not h.get("current")]
            prior_employer_counts.append(len({h.get("company") for h in prior}))
            for h in prior:
                if h.get("company") == company_name:
                    internal_moves += 1
                else:
                    external_moves += 1

            activity = p.get("recent_activity", []) or []
            if activity:
                with_activity += 1
                total_posts += len(activity)
                for a in activity:
                    if a.get("type") == "job_change":
                        job_change_posts += 1
                    if a.get("topic"):
                        topic_counter[a["topic"]] += 1

        s.department_distribution = dict(dept_counter)
        s.seniority_distribution = dict(seniority_counter)
        s.location_distribution = dict(location_counter.most_common(10))
        s.top_skills = skill_counter.most_common(15)
        s.top_universities = university_counter.most_common(10)

        if tenures:
            s.avg_tenure_years = round(statistics.mean(tenures), 2)
            s.median_tenure_years = round(statistics.median(tenures), 2)
        s.tenure_by_department = {
            d: round(statistics.mean(v), 2) for d, v in tenure_by_dept.items() if v
        }
        s.flight_risk_by_department = {
            d: round(statistics.mean(v) * 100, 1) for d, v in risk_by_dept.items() if v
        }
        s.flight_risk_pct = round(elevated_risk / len(profiles) * 100, 1)
        s.open_to_opportunities_pct = round(open_to_opps / len(profiles) * 100, 1)

        total_moves = external_moves + internal_moves
        s.external_hire_ratio = round(external_moves / total_moves, 3) if total_moves else 0.0
        s.avg_prior_employers = (
            round(statistics.mean(prior_employer_counts), 2) if prior_employer_counts else 0.0
        )

        s.profiles_with_activity = with_activity
        s.avg_posts_per_active_profile = (
            round(total_posts / with_activity, 1) if with_activity else 0.0
        )
        s.job_change_signal_count = job_change_posts
        s.top_discussion_topics = topic_counter.most_common(8)

    def _analyze_postings(self, s: DatasetSummary, postings: list[dict]) -> None:
        s.postings_analyzed = len(postings)

        dept_counter = Counter()
        quarter_counter = Counter()
        seniority_counter = Counter()
        arrangement_counter = Counter()
        skill_counter = Counter()

        dtf_all: list[int] = []
        dtf_by_dept: dict[str, list[int]] = defaultdict(list)
        dtf_by_title: dict[str, list[int]] = defaultdict(list)
        title_dept: dict[str, str] = {}
        salary_by_seniority: dict[str, list[int]] = defaultdict(list)
        active = 0
        remote = 0

        for p in postings:
            dept = p.get("department", "Unknown")
            dept_counter[dept] += 1
            seniority_counter[p.get("seniority_level", "Unknown")] += 1
            arrangement_counter[p.get("work_arrangement", "Unknown")] += 1

            if p.get("status") == "active":
                active += 1
            if p.get("work_arrangement") == "Remote":
                remote += 1

            posted = p.get("posted_date", "")
            if len(posted) >= 7:
                try:
                    dt = datetime.strptime(posted[:10], "%Y-%m-%d")
                    quarter_counter[f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"] += 1
                except ValueError:
                    pass

            dtf = p.get("days_to_fill")
            if isinstance(dtf, (int, float)):
                dtf_all.append(int(dtf))
                dtf_by_dept[dept].append(int(dtf))
                title = p.get("title", "Unknown")
                dtf_by_title[title].append(int(dtf))
                title_dept[title] = dept

            sal = p.get("salary_range") or {}
            low, high = sal.get("low"), sal.get("high")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                salary_by_seniority[p.get("seniority_level", "Unknown")].append(
                    int((low + high) / 2)
                )

            for skill in p.get("skills_required", []):
                skill_counter[skill] += 1

        s.postings_by_department = dict(dept_counter)
        s.postings_by_quarter = dict(sorted(quarter_counter.items()))
        s.seniority_demand = dict(seniority_counter)
        s.work_arrangement_mix = dict(arrangement_counter)
        s.active_postings = active
        s.remote_pct = round(remote / len(postings) * 100, 1)
        s.top_demanded_skills = skill_counter.most_common(15)

        if dtf_all:
            s.avg_days_to_fill = round(statistics.mean(dtf_all), 1)
        s.days_to_fill_by_department = {
            d: round(statistics.mean(v), 1) for d, v in dtf_by_dept.items() if v
        }
        s.avg_salary_by_seniority = {
            k: int(statistics.mean(v)) for k, v in salary_by_seniority.items() if v
        }

        # Hardest to fill: titles with >=3 postings, ranked by days to fill
        hardest = []
        for title, values in dtf_by_title.items():
            if len(values) >= 3:
                hardest.append({
                    "title": title,
                    "department": title_dept.get(title, "Unknown"),
                    "avg_days_to_fill": round(statistics.mean(values), 1),
                    "count": len(values),
                })
        hardest.sort(key=lambda x: -x["avg_days_to_fill"])
        s.hardest_to_fill = hardest[:10]

        # Hiring velocity: compare the last two *complete* quarters.
        # The newest and oldest quarters in the window are usually partial —
        # including them reads as a false spike or collapse.
        quarters = sorted(quarter_counter.keys())
        if len(quarters) >= 4:
            complete = quarters[1:-1]
            recent = quarter_counter[complete[-1]]
            prior = quarter_counter[complete[-2]]
            if prior:
                change = (recent - prior) / prior
                if change > 0.15:
                    s.hiring_velocity_trend = (
                        f"Accelerating (+{change:.0%} vs prior quarter)"
                    )
                elif change < -0.15:
                    s.hiring_velocity_trend = (
                        f"Slowing ({change:.0%} vs prior quarter)"
                    )
                else:
                    s.hiring_velocity_trend = (
                        f"Stable quarter over quarter ({change:+.0%})"
                    )
        if not s.hiring_velocity_trend:
            s.hiring_velocity_trend = "Insufficient history"

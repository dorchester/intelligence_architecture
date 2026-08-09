"""Guardrail engine.

Loads guardrails/config.yaml and enforces validation rules at defined
points in the workflow. Engineers tune behavior via the YAML file;
application code calls the check_* methods.

Design:
- Every check returns a GuardrailResult (never raises for policy failures)
- Severity determines caller behavior: block halts, warn annotates, log records
- Checks are individually toggleable so engineers can disable one without
  editing code
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

# Common keyboard-adjacency runs used to detect mashing
_KEYBOARD_RUNS = [
    "qwerty", "qwertyui", "asdf", "asdfgh", "asdfjkl", "zxcv", "zxcvbn",
    "wasd", "hjkl", "poiuy", "lkjh", "mnbv", "1234", "12345", "abcd",
]

_VOWELS = set("aeiouyAEIOUY")


@dataclass
class GuardrailViolation:
    """A single guardrail failure."""

    check: str
    severity: str  # block | warn | log
    message: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class GuardrailResult:
    """Outcome of running one or more guardrail checks."""

    violations: list[GuardrailViolation] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(v.severity == "block" for v in self.violations)

    @property
    def warnings(self) -> list[GuardrailViolation]:
        return [v for v in self.violations if v.severity == "warn"]

    @property
    def blocking_violations(self) -> list[GuardrailViolation]:
        return [v for v in self.violations if v.severity == "block"]

    def add(self, check: str, severity: str, message: str, detail: str = "") -> None:
        self.violations.append(GuardrailViolation(check, severity, message, detail))

    def merge(self, other: "GuardrailResult") -> "GuardrailResult":
        self.violations.extend(other.violations)
        return self

    def summary(self) -> str:
        if not self.violations:
            return "All guardrails passed"
        parts = []
        for v in self.violations:
            parts.append(f"[{v.severity.upper()}] {v.message}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "violations": [v.to_dict() for v in self.violations],
        }


class GuardrailEngine:
    """Loads and enforces guardrail configuration."""

    def __init__(self, config_path: Path | str | None = None):
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def reload(self) -> None:
        """Re-read the config file. Lets engineers tune without restarting."""
        self.config = self._load_config()

    def _rule(self, section: str, name: str) -> dict | None:
        """Fetch a rule config if it exists and is enabled."""
        rule = self.config.get(section, {}).get(name)
        if not rule or not rule.get("enabled", False):
            return None
        return rule

    def _severity(self, rule: dict, default: str = "warn") -> str:
        return rule.get("severity", default)

    # ========================================================
    # INPUT VALIDATION
    # ========================================================

    def check_company_name(self, name: str, is_preset: bool = False) -> GuardrailResult:
        """Validate a company name before spending any tokens on it."""
        result = GuardrailResult()
        rule = self._rule("input_validation", "company_name_format")
        if not rule:
            return result

        sev = self._severity(rule, "block")
        stripped = name.strip()
        lowered = stripped.lower()

        if is_preset and self.config.get("entity_verification", {}).get(
            "company_exists", {}
        ).get("trust_preset_companies", True):
            return result

        min_len = rule.get("min_length", 2)
        max_len = rule.get("max_length", 120)
        if len(stripped) < min_len:
            result.add(
                "company_name_format", sev,
                "Company name is too short",
                f"Got {len(stripped)} characters, minimum is {min_len}",
            )
            return result

        if len(stripped) > max_len:
            result.add(
                "company_name_format", sev,
                "Company name is too long",
                f"Got {len(stripped)} characters, maximum is {max_len}",
            )
            return result

        # Blocklist
        for term in rule.get("blocklist", []):
            if lowered == term.lower() or lowered.startswith(term.lower() + " "):
                result.add(
                    "company_name_format", sev,
                    "Company name appears to be a placeholder",
                    f"'{stripped}' matches blocked term '{term}'",
                )
                return result

        # Alphabetic ratio
        min_alpha = rule.get("min_alpha_ratio", 0.5)
        alpha_count = sum(1 for c in stripped if c.isalpha())
        if stripped and (alpha_count / len(stripped)) < min_alpha:
            result.add(
                "company_name_format", sev,
                "Company name contains too few letters",
                f"Only {alpha_count}/{len(stripped)} characters are alphabetic",
            )
            return result

        # Vowel requirement — catches consonant mashing like "sdkfjhsdkf"
        if rule.get("require_vowels", True):
            for token in re.findall(r"[A-Za-z]{4,}", stripped):
                if not any(c in _VOWELS for c in token):
                    result.add(
                        "company_name_format", sev,
                        "Company name looks like gibberish",
                        f"Token '{token}' contains no vowels",
                    )
                    return result

        # Repeated characters
        max_repeat = rule.get("max_char_repeat", 4)
        if re.search(rf"(.)\1{{{max_repeat},}}", stripped):
            result.add(
                "company_name_format", sev,
                "Company name has excessive repeated characters",
                f"More than {max_repeat} identical characters in a row",
            )
            return result

        # Keyboard patterns
        if rule.get("reject_keyboard_patterns", True):
            compact = re.sub(r"[^a-z]", "", lowered)
            for run in _KEYBOARD_RUNS:
                if len(run) >= 4 and run in compact:
                    result.add(
                        "company_name_format", sev,
                        "Company name appears to be keyboard input",
                        f"Contains keyboard pattern '{run}'",
                    )
                    return result

        return result

    def check_prompt_injection(self, fields: dict[str, str]) -> GuardrailResult:
        """Scan input fields for prompt-injection attempts."""
        result = GuardrailResult()
        rule = self._rule("input_validation", "prompt_injection")
        if not rule:
            return result

        sev = self._severity(rule, "block")
        scan_fields = rule.get("scan_fields", list(fields.keys()))
        patterns = rule.get("patterns", [])

        for field_name in scan_fields:
            value = fields.get(field_name)
            if not value:
                continue
            for pattern in patterns:
                try:
                    if re.search(pattern, value, re.IGNORECASE):
                        result.add(
                            "prompt_injection", sev,
                            "Input contains a prompt-injection pattern",
                            f"Field '{field_name}' matched pattern: {pattern}",
                        )
                        return result
                except re.error:
                    continue

        return result

    def check_engagement_context(self, context: str) -> GuardrailResult:
        """Validate the free-text engagement context field."""
        result = GuardrailResult()
        rule = self._rule("input_validation", "engagement_context_length")
        if not rule or not context:
            return result

        max_chars = rule.get("max_chars", 2000)
        if len(context) > max_chars:
            result.add(
                "engagement_context_length", self._severity(rule),
                "Engagement context is unusually long",
                f"{len(context)} characters exceeds recommended {max_chars}",
            )
        return result

    def validate_input(
        self, company_name: str, engagement_context: str = "", is_preset: bool = False
    ) -> GuardrailResult:
        """Run all input-validation checks in one call."""
        result = GuardrailResult()
        result.merge(self.check_company_name(company_name, is_preset=is_preset))
        if result.blocked:
            return result
        result.merge(
            self.check_prompt_injection(
                {"company_name": company_name, "engagement_context": engagement_context}
            )
        )
        if result.blocked:
            return result
        result.merge(self.check_engagement_context(engagement_context))
        return result

    # ========================================================
    # ENTITY VERIFICATION (LLM-assisted)
    # ========================================================

    def entity_verification_enabled(self) -> bool:
        return self._rule("entity_verification", "company_exists") is not None

    def entity_verification_config(self) -> dict:
        return self._rule("entity_verification", "company_exists") or {}

    def check_company_exists(
        self, company_name: str, model, is_preset: bool = False
    ) -> tuple[GuardrailResult, dict]:
        """Ask a cheap model whether this is a real, identifiable company.

        Returns (result, verification_payload).
        """
        result = GuardrailResult()
        rule = self._rule("entity_verification", "company_exists")
        if not rule:
            return result, {}

        if is_preset and rule.get("trust_preset_companies", True):
            return result, {"verified": True, "confidence": 1.0, "source": "preset_dataset"}

        sev = self._severity(rule, "block")
        min_conf = rule.get("min_confidence", 0.6)
        allow_fictional = rule.get("allow_fictional", False)

        prompt = (
            f'Is "{company_name}" a real, identifiable organization?\n\n'
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "is_real": true/false,\n'
            '  "confidence": 0.0-1.0,\n'
            '  "canonical_name": "Official name if real, else null",\n'
            '  "industry": "Primary industry if known, else null",\n'
            '  "reasoning": "One sentence",\n'
            '  "is_fictional": true/false\n'
            "}\n\n"
            "Set is_real=false for gibberish, placeholders, or names you cannot identify.\n"
            "Set is_fictional=true for known fictional companies (e.g. Acme Corp, Initech, Wayne Enterprises)."
        )

        try:
            response = model.invoke(
                messages=[{"role": "user", "content": prompt}],
                system="You verify whether organizations exist. Return only valid JSON. Be strict.",
                max_tokens=400,
                temperature=0.0,
            )
            payload = _parse_json(response)
        except Exception as e:
            result.add(
                "company_exists", "warn",
                "Could not verify company existence",
                f"Verification call failed: {type(e).__name__}",
            )
            return result, {"verified": None, "error": str(e)}

        if not payload or "is_real" not in payload:
            result.add(
                "company_exists", "warn",
                "Company verification returned an unparseable response",
                "",
            )
            return result, {"verified": None}

        is_real = bool(payload.get("is_real"))
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        is_fictional = bool(payload.get("is_fictional"))
        reasoning = payload.get("reasoning", "")

        if is_fictional and not allow_fictional:
            result.add(
                "company_exists", sev,
                f"'{company_name}' appears to be a fictional company",
                reasoning,
            )
            return result, payload

        if not is_real:
            result.add(
                "company_exists", sev,
                f"Could not identify '{company_name}' as a real organization",
                reasoning,
            )
            return result, payload

        if confidence < min_conf:
            ambiguous_action = rule.get("ambiguous_action", "confirm_with_user")
            if ambiguous_action == "confirm_with_user":
                result.add(
                    "company_exists", "warn",
                    f"Low confidence identifying '{company_name}' — please confirm",
                    f"Confidence {confidence:.2f} below threshold {min_conf}. {reasoning}",
                )
            else:
                result.add(
                    "company_exists", sev,
                    f"Low confidence identifying '{company_name}'",
                    f"Confidence {confidence:.2f} below threshold {min_conf}",
                )

        payload["verified"] = is_real and confidence >= min_conf
        return result, payload

    def check_company_size(
        self, reported_headcount: Any, preset_headcount: int | None = None
    ) -> GuardrailResult:
        """Sanity-check a headcount figure returned by research."""
        result = GuardrailResult()
        rule = self._rule("entity_verification", "company_size_sanity")
        if not rule:
            return result

        sev = self._severity(rule)
        parsed = _parse_headcount(reported_headcount)
        if parsed is None:
            return result

        min_hc = rule.get("min_headcount", 1)
        max_hc = rule.get("max_headcount", 3_000_000)

        if parsed < min_hc or parsed > max_hc:
            result.add(
                "company_size_sanity", sev,
                "Reported headcount is outside plausible range",
                f"Got {parsed:,}, expected between {min_hc:,} and {max_hc:,}",
            )
            return result

        if preset_headcount:
            threshold = rule.get("preset_deviation_threshold", 0.5)
            deviation = abs(parsed - preset_headcount) / max(preset_headcount, 1)
            if deviation > threshold:
                result.add(
                    "company_size_sanity", sev,
                    "Research headcount differs substantially from dataset",
                    f"Research: {parsed:,} vs dataset: {preset_headcount:,} "
                    f"({deviation:.0%} deviation)",
                )

        return result

    # ========================================================
    # OUTPUT VALIDATION
    # ========================================================

    def check_research_completeness(self, research: dict) -> GuardrailResult:
        """Verify research returned the fields downstream phases depend on."""
        result = GuardrailResult()
        rule = self._rule("output_validation", "research_completeness")
        if not rule:
            return result

        sev = self._severity(rule)
        missing = [
            f for f in rule.get("required_fields", [])
            if not research.get(f)
        ]
        if missing:
            result.add(
                "research_completeness", sev,
                "Research output is missing required fields",
                f"Missing: {', '.join(missing)}",
            )
        return result

    def research_max_retries(self) -> int:
        rule = self._rule("output_validation", "research_completeness")
        return rule.get("max_retries", 0) if rule else 0

    def check_hallucination_markers(self, text: str, label: str = "output") -> GuardrailResult:
        """Flag language suggesting the model is uncertain or inventing."""
        result = GuardrailResult()
        rule = self._rule("output_validation", "hallucination_markers")
        if not rule or not text:
            return result

        sev = self._severity(rule)
        hits = []
        for pattern in rule.get("patterns", []):
            try:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    hits.append(m.group(0)[:60])
            except re.error:
                continue

        if hits:
            result.add(
                "hallucination_markers", sev,
                f"Possible uncertainty or placeholder text in {label}",
                f"Found: {'; '.join(hits[:3])}",
            )
        return result

    def check_pii(self, text: str, label: str = "output") -> GuardrailResult:
        """Block real personal data from appearing in output."""
        result = GuardrailResult()
        rule = self._rule("output_validation", "pii_leakage")
        if not rule or not text:
            return result

        sev = self._severity(rule, "block")
        for pattern in rule.get("patterns", []):
            try:
                if re.search(pattern, text):
                    result.add(
                        "pii_leakage", sev,
                        f"Potential PII detected in {label}",
                        "Output withheld pending review",
                    )
                    return result
            except re.error:
                continue
        return result

    def check_minimum_length(self, text: str, kind: str = "briefing") -> GuardrailResult:
        """Catch truncated or empty LLM responses."""
        result = GuardrailResult()
        rule = self._rule("output_validation", "minimum_length")
        if not rule:
            return result

        key = "briefing_min_chars" if kind == "briefing" else "analysis_min_chars"
        minimum = rule.get(key, 0)
        length = len(text or "")
        if length < minimum:
            result.add(
                "minimum_length", self._severity(rule),
                f"{kind.title()} output is shorter than expected",
                f"{length} characters, expected at least {minimum}",
            )
        return result

    def validate_output(self, text: str, kind: str = "briefing") -> GuardrailResult:
        """Run all output checks on a text response."""
        result = GuardrailResult()
        result.merge(self.check_pii(text, kind))
        if result.blocked:
            return result
        result.merge(self.check_hallucination_markers(text, kind))
        result.merge(self.check_minimum_length(text, kind))
        return result

    # ========================================================
    # COST CONTROLS
    # ========================================================

    def check_revision_limit(self, revision_count: int) -> GuardrailResult:
        """Prevent infinite revision loops at a checkpoint."""
        result = GuardrailResult()
        rule = self._rule("cost_controls", "max_revisions_per_checkpoint")
        if not rule:
            return result

        limit = rule.get("limit", 5)
        if revision_count >= limit:
            result.add(
                "max_revisions_per_checkpoint", self._severity(rule, "block"),
                f"Revision limit reached for this checkpoint",
                f"{revision_count} revisions, limit is {limit}. Approve or restart the run.",
            )
        return result

    def check_token_budget(self, tokens_used: int) -> GuardrailResult:
        """Enforce a hard ceiling on per-run token spend."""
        result = GuardrailResult()
        rule = self._rule("cost_controls", "max_tokens_per_run")
        if not rule:
            return result

        limit = rule.get("limit", 150_000)
        if tokens_used >= limit:
            result.add(
                "max_tokens_per_run", self._severity(rule, "block"),
                "Token budget exhausted for this run",
                f"{tokens_used:,} tokens used, limit is {limit:,}",
            )
        return result

    def max_concurrent_runs(self) -> int | None:
        rule = self._rule("cost_controls", "max_concurrent_runs")
        return rule.get("limit") if rule else None

    def check_concurrent_runs(self, active_count: int) -> GuardrailResult:
        result = GuardrailResult()
        rule = self._rule("cost_controls", "max_concurrent_runs")
        if not rule:
            return result
        limit = rule.get("limit", 10)
        if active_count >= limit:
            result.add(
                "max_concurrent_runs", self._severity(rule, "block"),
                "Too many runs in progress",
                f"{active_count} active, limit is {limit}. Wait for one to finish.",
            )
        return result

    # ========================================================
    # DATA ACCESS
    # ========================================================

    def dataset_limits(self) -> dict:
        rule = self._rule("data_access", "dataset_query_limits")
        if not rule:
            return {"max_profiles_returned": 1000, "max_postings_returned": 1000}
        return {
            "max_profiles_returned": rule.get("max_profiles_returned", 1000),
            "max_postings_returned": rule.get("max_postings_returned", 1000),
        }

    def cross_company_assertion_enabled(self) -> bool:
        rule = self._rule("data_access", "cross_company_isolation")
        return bool(rule and rule.get("enforce_assertion", True))


# ============================================================
# HELPERS
# ============================================================

def _parse_json(text: str) -> dict | None:
    import json

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("\n", 1)
        if len(parts) > 1:
            cleaned = parts[1].rsplit("```", 1)[0]
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                return None
    return None


def _parse_headcount(value: Any) -> int | None:
    """Extract an integer headcount from strings like '~43,000' or '40,000-45,000'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).lower().replace(",", "")
    # Handle "40000-45000" -> take midpoint
    range_match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)", text)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return (low + high) // 2

    # Handle "43k" / "1.2 million"
    if "million" in text:
        num = re.search(r"([\d.]+)", text)
        if num:
            return int(float(num.group(1)) * 1_000_000)
    if re.search(r"\d+k\b", text):
        num = re.search(r"([\d.]+)k", text)
        if num:
            return int(float(num.group(1)) * 1000)

    num = re.search(r"(\d+)", text)
    return int(num.group(1)) if num else None

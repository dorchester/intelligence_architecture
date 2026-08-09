"""Generate full-scale synthetic datasets for all 20 companies.

Generates:
- 500+ LinkedIn-style profiles per company (with job history, skills, activity)
- 500+ job postings per company (past 2 years)
- Uploads all to S3 under a structured prefix

Approach:
1. Use Bedrock to generate per-company "templates" (title ladders, posting patterns)
2. Use Python to generate volume deterministically from templates
3. Upload to S3 as JSONL files (one line per record, queryable)

Usage:
    python scripts/data_generation/generate_all.py --profile intelligence-dev
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.data_generation.companies import COMPANIES
from agent.model import BedrockModel, ModelConfig

# Deterministic seed per company for reproducibility
random.seed(42)

# Names pools
FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Christopher", "Karen", "Charles", "Lisa", "Daniel", "Nancy",
    "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
    "Steven", "Emily", "Paul", "Donna", "Andrew", "Michelle", "Joshua", "Carol",
    "Kenneth", "Amanda", "Kevin", "Dorothy", "Brian", "Melissa", "George", "Deborah",
    "Timothy", "Stephanie", "Ronald", "Rebecca", "Edward", "Sharon", "Jason", "Laura",
    "Jeffrey", "Cynthia", "Ryan", "Kathleen", "Jacob", "Amy", "Gary", "Angela",
    "Nicholas", "Shirley", "Eric", "Anna", "Jonathan", "Brenda", "Stephen", "Pamela",
    "Larry", "Emma", "Justin", "Nicole", "Scott", "Helen", "Brandon", "Samantha",
    "Benjamin", "Katherine", "Samuel", "Christine", "Raymond", "Debra", "Gregory", "Rachel",
    "Frank", "Carolyn", "Alexander", "Janet", "Patrick", "Catherine", "Jack", "Maria",
    "Aiden", "Priya", "Wei", "Fatima", "Raj", "Yuki", "Omar", "Mei",
    "Carlos", "Olga", "Ahmed", "Aisha", "Diego", "Sumi", "Ivan", "Leila",
    "Hiroshi", "Sofia", "Kwame", "Ingrid", "Mateo", "Nadia", "Liam", "Zara",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill",
    "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
    "Mitchell", "Carter", "Roberts", "Chen", "Kim", "Patel", "Singh", "Shah",
    "Zhang", "Li", "Wang", "Wu", "Liu", "Yang", "Huang", "Zhao",
    "O'Brien", "Murphy", "Sullivan", "McCarthy", "Fitzgerald", "Walsh", "Burke",
    "Cohen", "Goldstein", "Friedman", "Rosenberg", "Schwartz", "Adler",
    "Müller", "Schmidt", "Weber", "Fischer", "Meyer", "Wagner",
    "Tanaka", "Yamamoto", "Watanabe", "Takahashi", "Suzuki", "Nakamura",
    "Okafor", "Adeyemi", "Mensah", "Diallo", "Ibrahim",
]

UNIVERSITIES = [
    "Harvard University", "Stanford University", "MIT", "Yale University",
    "University of Michigan", "UC Berkeley", "Northwestern University",
    "Columbia University", "University of Pennsylvania", "Duke University",
    "University of Chicago", "Cornell University", "Georgetown University",
    "University of Virginia", "Vanderbilt University", "NYU",
    "University of Texas at Austin", "UCLA", "USC", "Georgia Tech",
    "University of Illinois", "Ohio State University", "Penn State",
    "University of Florida", "Indiana University", "Michigan State",
    "Arizona State University", "University of Washington", "Boston University",
    "Purdue University", "University of Wisconsin", "University of Minnesota",
    "Rutgers University", "University of Maryland", "Virginia Tech",
]

SKILLS_BY_DOMAIN = {
    "engineering": ["Python", "Java", "AWS", "Kubernetes", "CI/CD", "Microservices", "SQL", "React", "Node.js", "Terraform"],
    "finance": ["Financial Modeling", "Excel", "Bloomberg", "Risk Management", "Valuation", "M&A", "FP&A", "SAP", "Budgeting"],
    "operations": ["Six Sigma", "Lean", "Supply Chain", "Process Improvement", "ERP", "Logistics", "Vendor Management"],
    "sales": ["Salesforce", "Pipeline Management", "Negotiation", "Account Management", "B2B Sales", "CRM", "Lead Generation"],
    "hr": ["Workday", "Talent Acquisition", "Employee Relations", "Compensation", "HRIS", "Succession Planning", "DEI"],
    "marketing": ["Digital Marketing", "Brand Strategy", "Analytics", "SEO/SEM", "Content Strategy", "Social Media", "Market Research"],
    "consulting": ["Strategy", "Change Management", "Stakeholder Management", "Business Analysis", "Project Management", "Agile"],
    "healthcare": ["Clinical Operations", "Regulatory Affairs", "Patient Safety", "HIPAA", "EMR/EHR", "Quality Improvement"],
    "manufacturing": ["Lean Manufacturing", "Quality Control", "ISO 9001", "CAD/CAM", "Production Planning", "Safety"],
    "legal": ["Contract Negotiation", "Compliance", "Corporate Governance", "M&A", "IP Law", "Regulatory"],
}

POST_TYPES = ["thought_leadership", "job_change", "company_news", "industry_insight", "achievement", "event"]


def generate_company_template(model: BedrockModel, company: dict) -> dict:
    """Use LLM to generate realistic org-specific template for a company."""
    prompt = f"""Generate a realistic organizational template for a company like this:
Name: {company['name']}
Industry: {company['industry']}
Headcount: {company['headcount']}
Segments: {company['segments']}

Return JSON with:
{{
  "departments": [
    {{
      "name": "Department Name",
      "headcount_pct": 0.15,
      "title_ladder": ["Entry Title", "Mid Title", "Senior Title", "Director Title", "VP Title"],
      "common_skills": ["skill1", "skill2", "skill3"],
      "typical_degrees": ["degree1", "degree2"],
      "avg_tenure_years": 4.5,
      "turnover_rate": 0.15
    }}
  ],
  "posting_categories": [
    {{
      "category": "Category Name",
      "volume_pct": 0.20,
      "typical_titles": ["Title 1", "Title 2"],
      "locations": ["City, ST", "City, ST"],
      "remote_pct": 0.3
    }}
  ],
  "culture_keywords": ["keyword1", "keyword2", "keyword3"],
  "recent_initiatives": ["Initiative 1", "Initiative 2"]
}}

Include 8-12 departments and 6-8 posting categories. Make it specific to the {company['industry']} industry.
Return ONLY valid JSON."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="Generate realistic organizational data. Return only valid JSON.",
        max_tokens=3000,
        temperature=0.7,
    )

    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
        return None


def generate_profiles(company: dict, template: dict, count: int = 500) -> list[dict]:
    """Generate synthetic LinkedIn profiles deterministically from template."""
    profiles = []
    departments = template.get("departments", [])
    if not departments:
        return profiles

    company_seed = int(hashlib.md5(company["id"].encode()).hexdigest()[:8], 16)
    rng = random.Random(company_seed)

    for i in range(count):
        dept = rng.choices(departments, weights=[d.get("headcount_pct", 0.1) for d in departments])[0]
        title_ladder = dept.get("title_ladder", ["Employee"])
        seniority_idx = rng.choices(range(len(title_ladder)), weights=_seniority_weights(len(title_ladder)))[0]
        title = title_ladder[seniority_idx]

        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        age = 22 + seniority_idx * 5 + rng.randint(0, 8)
        tenure = round(rng.uniform(0.3, min(seniority_idx * 3 + 2, 20)), 1)

        # Job history (2-6 prior roles)
        num_prior = min(seniority_idx + rng.randint(0, 2), 5)
        job_history = _generate_job_history(rng, company, dept, title_ladder, seniority_idx, num_prior)

        # Skills
        domain_skills = dept.get("common_skills", [])
        general_skills = rng.sample(SKILLS_BY_DOMAIN.get("consulting", []), min(3, len(SKILLS_BY_DOMAIN["consulting"])))
        skills = list(set(domain_skills[:5] + general_skills[:3]))

        # Education
        degrees = dept.get("typical_degrees", ["Business Administration"])
        university = rng.choice(UNIVERSITIES)

        # Social activity (past 6 months)
        posts_per_month = rng.choices([0, 1, 2, 3, 5], weights=[30, 30, 20, 10, 10])[0]
        activity = _generate_activity(rng, company, dept, posts_per_month)

        # Turnover risk
        turnover_rate = dept.get("turnover_rate", 0.15)
        if tenure < 1.5:
            risk_score = min(turnover_rate * 3, 0.8)
        elif tenure > 8:
            risk_score = turnover_rate * 0.5
        else:
            risk_score = turnover_rate

        profile = {
            "profile_id": f"{company['id']}-{i+1:05d}",
            "first_name": first,
            "last_name": last,
            "full_name": f"{first} {last}",
            "headline": f"{title} at {company['name']}",
            "company": company["name"],
            "company_id": company["id"],
            "department": dept["name"],
            "title": title,
            "seniority_level": ["Entry", "Mid", "Senior", "Director", "VP/Executive"][min(seniority_idx, 4)],
            "location": rng.choice(["New York, NY", "Chicago, IL", "San Francisco, CA", "Dallas, TX", "Atlanta, GA", "Remote", company["hq"]]),
            "tenure_years": tenure,
            "age": age,
            "education": {
                "university": university,
                "degree": rng.choice(degrees) if degrees else "Business Administration",
                "graduation_year": max(2000, 2024 - (age - 22)),
            },
            "skills": skills,
            "connections": rng.randint(150, 2500) + seniority_idx * 300,
            "job_history": job_history,
            "recent_activity": activity,
            "turnover_risk_score": round(risk_score, 2),
            "open_to_opportunities": rng.random() < risk_score,
        }
        profiles.append(profile)

    return profiles


def generate_postings(company: dict, template: dict, count: int = 500) -> list[dict]:
    """Generate synthetic job postings for the past 2 years."""
    postings = []
    categories = template.get("posting_categories", [])
    if not categories:
        return postings

    company_seed = int(hashlib.md5((company["id"] + "-postings").encode()).hexdigest()[:8], 16)
    rng = random.Random(company_seed)

    base_date = datetime(2024, 8, 1)  # 2 years of postings from Aug 2024 to Aug 2026

    for i in range(count):
        cat = rng.choices(categories, weights=[c.get("volume_pct", 0.1) for c in categories])[0]
        titles = cat.get("typical_titles", ["Associate"])
        locations = cat.get("locations", [company["hq"]])
        remote_pct = cat.get("remote_pct", 0.2)

        # Random date in past 2 years
        days_ago = rng.randint(0, 730)
        posted_date = base_date + timedelta(days=730 - days_ago)

        # Status based on age
        if days_ago > 60:
            status = rng.choices(["filled", "closed", "expired"], weights=[60, 25, 15])[0]
        elif days_ago > 14:
            status = rng.choices(["active", "filled", "closed"], weights=[30, 50, 20])[0]
        else:
            status = "active"

        is_remote = rng.random() < remote_pct
        seniority = rng.choices(
            ["Entry Level", "Mid Level", "Senior", "Director", "VP"],
            weights=[25, 35, 25, 10, 5]
        )[0]

        # Salary range based on seniority
        base_salary = {"Entry Level": 55000, "Mid Level": 85000, "Senior": 120000, "Director": 160000, "VP": 220000}[seniority]
        salary_low = int(base_salary * rng.uniform(0.9, 1.0))
        salary_high = int(base_salary * rng.uniform(1.1, 1.4))

        posting = {
            "posting_id": f"{company['id']}-post-{i+1:05d}",
            "company": company["name"],
            "company_id": company["id"],
            "title": rng.choice(titles),
            "department": cat["category"],
            "location": "Remote" if is_remote else rng.choice(locations),
            "work_arrangement": "Remote" if is_remote else rng.choices(["On-site", "Hybrid"], weights=[40, 60])[0],
            "seniority_level": seniority,
            "posted_date": posted_date.strftime("%Y-%m-%d"),
            "status": status,
            "salary_range": {"low": salary_low, "high": salary_high, "currency": "USD"},
            "applicant_count": rng.randint(20, 500) if status != "active" else rng.randint(5, 150),
            "days_to_fill": rng.randint(15, 90) if status == "filled" else None,
            "skills_required": rng.sample(
                SKILLS_BY_DOMAIN.get(cat["category"].lower().split()[0], SKILLS_BY_DOMAIN["consulting"]),
                min(4, len(SKILLS_BY_DOMAIN["consulting"]))
            ),
            "segment": rng.choice(company["segments"]),
        }
        postings.append(posting)

    return postings


def _seniority_weights(n: int) -> list[float]:
    """Pyramid distribution: more junior, fewer senior."""
    if n <= 1:
        return [1.0]
    weights = [max(0.1, 1.0 - (i / n) * 0.8) for i in range(n)]
    weights[0] *= 1.5  # Extra entry-level
    return weights


def _generate_job_history(rng, company, dept, title_ladder, current_idx, num_prior):
    """Generate realistic job history."""
    history = []
    current_year = 2026

    # Current role
    start_year = current_year - rng.randint(1, 4)
    history.append({
        "company": company["name"],
        "title": title_ladder[current_idx],
        "start_year": start_year,
        "end_year": None,
        "current": True,
    })

    # Prior roles (mix of internal promotions and external moves)
    year = start_year
    for j in range(num_prior):
        duration = rng.randint(1, 4)
        year -= duration
        if year < 2005:
            break

        if rng.random() < 0.4 and current_idx - j - 1 >= 0:
            # Internal promotion
            history.append({
                "company": company["name"],
                "title": title_ladder[max(0, current_idx - j - 1)],
                "start_year": year,
                "end_year": year + duration,
                "current": False,
            })
        else:
            # External role
            ext_companies = ["Acme Corp", "Global Solutions Inc", "TechForward", "Apex Industries",
                           "Summit Group", "Catalyst Partners", "Frontier Corp", "Synergy Holdings"]
            history.append({
                "company": rng.choice(ext_companies),
                "title": title_ladder[max(0, current_idx - j - 1)] if current_idx - j - 1 >= 0 else "Analyst",
                "start_year": year,
                "end_year": year + duration,
                "current": False,
            })

    return history


def _generate_activity(rng, company, dept, posts_per_month):
    """Generate LinkedIn-style social activity."""
    if posts_per_month == 0:
        return []

    activity = []
    for month_offset in range(6):
        num_posts = rng.randint(0, posts_per_month)
        for _ in range(num_posts):
            post_type = rng.choice(POST_TYPES)
            activity.append({
                "type": post_type,
                "month": f"2026-{8 - month_offset:02d}",
                "engagement": {
                    "likes": rng.randint(5, 200),
                    "comments": rng.randint(0, 30),
                    "shares": rng.randint(0, 10),
                },
                "topic": _generate_topic(rng, post_type, company, dept),
            })

    return activity


def _generate_topic(rng, post_type, company, dept):
    topics = {
        "thought_leadership": [
            f"The future of {dept['name'].lower()} in {company['industry'].split('/')[0].strip()}",
            "AI's impact on our industry",
            "Leadership lessons from recent transformation",
            "Why organizational agility matters now",
        ],
        "job_change": [
            f"Excited to join {company['name']}",
            "New chapter in my career",
            f"Thrilled to take on a new role in {dept['name']}",
        ],
        "company_news": [
            f"{company['name']} announces new initiative",
            "Proud of our team's Q3 results",
            f"Great work from the {dept['name']} team",
        ],
        "industry_insight": [
            f"Key trends in {company['industry'].split('/')[0].strip()} for 2026",
            "What the latest research tells us",
            "Lessons from a decade in this industry",
        ],
        "achievement": [
            "Honored to receive this recognition",
            "Our team just hit a major milestone",
            "Certification complete!",
        ],
        "event": [
            "Great insights at this week's conference",
            "Panel discussion on workforce transformation",
            "Networking at industry summit",
        ],
    }
    return rng.choice(topics.get(post_type, ["Professional update"]))


def upload_to_s3(profiles: list, postings: list, company: dict, bucket: str, session):
    """Upload profiles and postings as JSONL to S3."""
    s3 = session.client("s3")

    # Upload profiles
    profiles_key = f"datasets/{company['id']}/profiles.jsonl"
    profiles_data = "\n".join(json.dumps(p) for p in profiles)
    s3.put_object(Bucket=bucket, Key=profiles_key, Body=profiles_data.encode())

    # Upload postings
    postings_key = f"datasets/{company['id']}/postings.jsonl"
    postings_data = "\n".join(json.dumps(p) for p in postings)
    s3.put_object(Bucket=bucket, Key=postings_key, Body=postings_data.encode())

    # Upload company metadata
    meta_key = f"datasets/{company['id']}/metadata.json"
    meta = {
        "company_id": company["id"],
        "company_name": company["name"],
        "industry": company["industry"],
        "headcount": company["headcount"],
        "hq": company["hq"],
        "segments": company["segments"],
        "profile_count": len(profiles),
        "posting_count": len(postings),
        "generated_at": datetime.utcnow().isoformat(),
    }
    s3.put_object(Bucket=bucket, Key=meta_key, Body=json.dumps(meta, indent=2).encode())

    return profiles_key, postings_key


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic datasets for all 20 companies")
    parser.add_argument("--profile", default="intelligence-dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profiles-per-company", type=int, default=500)
    parser.add_argument("--postings-per-company", type=int, default=500)
    parser.add_argument("--skip-s3", action="store_true", help="Generate locally only (no S3 upload)")
    parser.add_argument("--company", help="Generate for a single company ID only")
    args = parser.parse_args()

    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)

    # Get bucket name
    if not args.skip_s3:
        cfn = session.client("cloudformation")
        resp = cfn.describe_stacks(StackName="intelligence-engine-dev-storage")
        bucket = [o["OutputValue"] for o in resp["Stacks"][0]["Outputs"] if o["OutputKey"] == "BucketName"][0]
    else:
        bucket = None

    # Set up Bedrock model for template generation
    model_config = ModelConfig(model_id="us.anthropic.claude-sonnet-4-6", profile=args.profile, region=args.region)
    model = BedrockModel(model_config)

    companies = COMPANIES
    if args.company:
        companies = [c for c in COMPANIES if c["id"] == args.company]
        if not companies:
            print(f"Company '{args.company}' not found")
            return

    total_profiles = 0
    total_postings = 0

    print(f"Generating synthetic data for {len(companies)} companies")
    print(f"  Profiles per company: {args.profiles_per_company}")
    print(f"  Postings per company: {args.postings_per_company}")
    print(f"  S3 bucket: {bucket or 'SKIP'}")
    print()

    # Generate manifest
    manifest = []

    for i, company in enumerate(companies):
        print(f"[{i+1}/{len(companies)}] {company['name']}...")

        # Step 1: Generate template via LLM
        print(f"  Generating org template via Bedrock...")
        template = generate_company_template(model, company)
        if not template:
            print(f"  WARNING: Template generation failed, using fallback")
            template = _fallback_template(company)

        # Step 2: Generate profiles
        print(f"  Generating {args.profiles_per_company} profiles...")
        profiles = generate_profiles(company, template, count=args.profiles_per_company)

        # Step 3: Generate postings
        print(f"  Generating {args.postings_per_company} postings...")
        postings = generate_postings(company, template, count=args.postings_per_company)

        # Step 4: Upload to S3
        if bucket:
            print(f"  Uploading to S3...")
            upload_to_s3(profiles, postings, company, bucket, session)

        total_profiles += len(profiles)
        total_postings += len(postings)

        manifest.append({
            "company_id": company["id"],
            "company_name": company["name"],
            "industry": company["industry"],
            "headcount": company["headcount"],
            "profile_count": len(profiles),
            "posting_count": len(postings),
        })

        print(f"  Done: {len(profiles)} profiles, {len(postings)} postings")
        print()

    # Upload manifest
    if bucket:
        manifest_data = json.dumps(manifest, indent=2)
        session.client("s3").put_object(
            Bucket=bucket, Key="datasets/manifest.json", Body=manifest_data.encode()
        )
        print(f"Manifest uploaded to s3://{bucket}/datasets/manifest.json")

    print(f"\n{'='*60}")
    print(f"  COMPLETE")
    print(f"  Companies: {len(companies)}")
    print(f"  Total profiles: {total_profiles:,}")
    print(f"  Total postings: {total_postings:,}")
    if bucket:
        print(f"  S3 location: s3://{bucket}/datasets/")
    print(f"{'='*60}")


def _fallback_template(company):
    """Fallback template if LLM generation fails."""
    return {
        "departments": [
            {"name": "Engineering", "headcount_pct": 0.2, "title_ladder": ["Engineer", "Senior Engineer", "Staff Engineer", "Director of Engineering", "VP Engineering"], "common_skills": ["Python", "AWS", "SQL"], "typical_degrees": ["Computer Science", "Software Engineering"], "avg_tenure_years": 3.5, "turnover_rate": 0.18},
            {"name": "Sales", "headcount_pct": 0.15, "title_ladder": ["SDR", "Account Executive", "Senior AE", "Sales Director", "VP Sales"], "common_skills": ["Salesforce", "Negotiation", "Pipeline Management"], "typical_degrees": ["Business Administration", "Marketing"], "avg_tenure_years": 2.5, "turnover_rate": 0.25},
            {"name": "Operations", "headcount_pct": 0.2, "title_ladder": ["Operations Analyst", "Operations Manager", "Senior Manager", "Director of Operations", "VP Operations"], "common_skills": ["Process Improvement", "Six Sigma", "ERP"], "typical_degrees": ["Industrial Engineering", "Business"], "avg_tenure_years": 4.0, "turnover_rate": 0.12},
            {"name": "Finance", "headcount_pct": 0.1, "title_ladder": ["Financial Analyst", "Senior Analyst", "Finance Manager", "Controller", "CFO"], "common_skills": ["Financial Modeling", "Excel", "SAP"], "typical_degrees": ["Finance", "Accounting"], "avg_tenure_years": 4.5, "turnover_rate": 0.10},
            {"name": "HR", "headcount_pct": 0.08, "title_ladder": ["HR Coordinator", "HR Business Partner", "HR Manager", "HR Director", "CHRO"], "common_skills": ["Workday", "Talent Acquisition", "Employee Relations"], "typical_degrees": ["Human Resources", "Psychology"], "avg_tenure_years": 3.8, "turnover_rate": 0.15},
            {"name": "Marketing", "headcount_pct": 0.1, "title_ladder": ["Marketing Coordinator", "Marketing Manager", "Senior Manager", "Director of Marketing", "CMO"], "common_skills": ["Digital Marketing", "Analytics", "Brand Strategy"], "typical_degrees": ["Marketing", "Communications"], "avg_tenure_years": 3.0, "turnover_rate": 0.20},
            {"name": "Legal", "headcount_pct": 0.05, "title_ladder": ["Paralegal", "Associate Counsel", "Senior Counsel", "Deputy GC", "General Counsel"], "common_skills": ["Contract Negotiation", "Compliance", "Corporate Law"], "typical_degrees": ["Law (JD)", "Legal Studies"], "avg_tenure_years": 5.0, "turnover_rate": 0.08},
            {"name": "R&D", "headcount_pct": 0.12, "title_ladder": ["Research Associate", "Scientist", "Senior Scientist", "Principal Scientist", "VP R&D"], "common_skills": ["Research Methods", "Data Analysis", "Patent Development"], "typical_degrees": ["PhD in relevant field", "MS in Science/Engineering"], "avg_tenure_years": 5.5, "turnover_rate": 0.10},
        ],
        "posting_categories": [
            {"category": "Technology", "volume_pct": 0.25, "typical_titles": ["Software Engineer", "Data Engineer", "Cloud Architect"], "locations": [company["hq"], "Remote"], "remote_pct": 0.4},
            {"category": "Operations", "volume_pct": 0.20, "typical_titles": ["Operations Manager", "Supply Chain Analyst", "Logistics Coordinator"], "locations": [company["hq"]], "remote_pct": 0.1},
            {"category": "Sales", "volume_pct": 0.20, "typical_titles": ["Account Executive", "Sales Manager", "Business Development Rep"], "locations": [company["hq"], "New York, NY", "Chicago, IL"], "remote_pct": 0.3},
            {"category": "Corporate", "volume_pct": 0.15, "typical_titles": ["Financial Analyst", "HR Business Partner", "Legal Counsel"], "locations": [company["hq"]], "remote_pct": 0.2},
            {"category": "Research", "volume_pct": 0.10, "typical_titles": ["Research Scientist", "Lab Technician", "Clinical Researcher"], "locations": [company["hq"]], "remote_pct": 0.05},
            {"category": "Executive", "volume_pct": 0.10, "typical_titles": ["Vice President", "Senior Director", "Chief of Staff"], "locations": [company["hq"]], "remote_pct": 0.2},
        ],
    }


if __name__ == "__main__":
    main()

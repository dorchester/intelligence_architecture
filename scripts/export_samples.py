"""Export sample dataset records from S3 into docs/samples/.

Committed samples let people read the repository and understand exactly what
the system produces without needing AWS credentials.

Usage:
    python scripts/export_samples.py --profile intelligence-dev
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

OUT_DIR = BASE_DIR / "docs" / "samples"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export dataset samples for the repo")
    parser.add_argument("--profile", default="intelligence-dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--company", default="sterling-pharma",
                        help="Which company to draw record samples from")
    parser.add_argument("--records", type=int, default=3,
                        help="How many profiles/postings to include")
    args = parser.parse_args()

    import boto3
    from datasets.query import DatasetQuery

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    cfn = session.client("cloudformation")
    bucket = next(
        o["OutputValue"]
        for o in cfn.describe_stacks(
            StackName="intelligence-engine-dev-storage"
        )["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "BucketName"
    )

    dq = DatasetQuery(bucket=bucket, region=args.region, profile=args.profile)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def write(name: str, content: str) -> None:
        path = OUT_DIR / name
        path.write_text(content, encoding="utf-8")
        print(f"  {name}  ({path.stat().st_size:,} bytes)")

    print(f"Exporting samples from {args.company}")

    manifest = dq.list_companies()
    write("dataset_manifest.json", json.dumps(manifest, indent=2))

    profiles = dq.get_profiles(args.company, limit=args.records)
    write("sample_profiles.json", json.dumps(profiles, indent=2))

    postings = dq.get_postings(args.company, limit=args.records)
    write("sample_postings.json", json.dumps(postings, indent=2))

    summary = dq.summarize(args.company)
    if summary:
        write("sample_dataset_summary.json", json.dumps(summary.to_dict(), indent=2))
        write("sample_agent_context.txt", summary.to_agent_context())

    print(f"\nWrote to {OUT_DIR}")


if __name__ == "__main__":
    main()

"""Find project resources on AWS that no CloudFormation stack owns.

    python scripts/iac_coverage.py --profile intelligence-dev

The repository is meant to be the complete description of what runs. Anything
this reports is drift: a resource created by hand or by a script that no
template would recreate, which means a rebuild from source would not produce
the same account.

Read-only. Non-zero exit when unmanaged resources are found.
"""
from __future__ import annotations

import argparse
import sys

import boto3

PREFIX_S = "intelligence-engine-"
PREFIX_U = "intelligence_engine"
PREFIX_SHORT = "ie_"

drift: list[str] = []


def report(kind: str, name: str, managed: bool, note: str = "") -> None:
    if not managed:
        drift.append(f"{kind}: {name}")
    flag = "managed" if managed else "UNMANAGED"
    print(f"  [{flag:9}] {kind:22} {name}" + (f"  ({note})" if note else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    kw = {"region_name": args.region}
    if args.profile:
        kw["profile_name"] = args.profile
    s = boto3.Session(**kw)

    print("=== stacks ===")
    cfn = s.client("cloudformation")
    stacks = [st for st in cfn.describe_stacks()["Stacks"]
              if st["StackName"].startswith(PREFIX_S)]
    for st in stacks:
        print(f"  {st['StackName']:48} {st['StackStatus']}")

    # Every physical resource CloudFormation knows about.
    owned: set[str] = set()
    for st in stacks:
        for page in cfn.get_paginator("list_stack_resources").paginate(
                StackName=st["StackName"]):
            for r in page["StackResourceSummaries"]:
                pid = r.get("PhysicalResourceId")
                if pid:
                    owned.add(pid)
                    owned.add(pid.rsplit("/", 1)[-1])

    print("\n=== S3 buckets ===")
    for b in s.client("s3").list_buckets()["Buckets"]:
        if b["Name"].startswith(PREFIX_S):
            report("bucket", b["Name"], b["Name"] in owned)

    print("\n=== IAM roles ===")
    iam = s.client("iam")
    for page in iam.get_paginator("list_roles").paginate():
        for r in page["Roles"]:
            if r["RoleName"].startswith(PREFIX_S):
                report("role", r["RoleName"], r["RoleName"] in owned)

    print("\n=== IAM managed policies ===")
    for page in iam.get_paginator("list_policies").paginate(Scope="Local"):
        for p in page["Policies"]:
            if p["PolicyName"].startswith(PREFIX_S):
                report("policy", p["PolicyName"], p["Arn"] in owned)

    print("\n=== Glue databases ===")
    glue = s.client("glue")
    for page in glue.get_paginator("get_databases").paginate():
        for d in page["DatabaseList"]:
            n = d["Name"]
            if n.startswith(PREFIX_U) or n.startswith(PREFIX_SHORT):
                report("glue database", n, n in owned)

    print("\n=== Glue tables (data, expected to be stage-created) ===")
    for page in glue.get_paginator("get_databases").paginate():
        for d in page["DatabaseList"]:
            if not (d["Name"].startswith(PREFIX_U) or d["Name"].startswith(PREFIX_SHORT)):
                continue
            try:
                for t in glue.get_tables(DatabaseName=d["Name"])["TableList"]:
                    print(f"  [data     ] table                  {d['Name']}.{t['Name']}"
                          "  (written by a stage in stages/)")
            except Exception:
                pass

    print("\n=== SSM parameters ===")
    ssm = s.client("ssm")
    for page in ssm.get_paginator("describe_parameters").paginate(
            ParameterFilters=[{"Key": "Name", "Option": "BeginsWith",
                               "Values": ["/intelligence-engine/"]}]):
        for p in page["Parameters"]:
            # Config values are data, not infrastructure - but they must be
            # reproducible from a script in the repository, or a rebuild
            # silently produces stages that cannot resolve anything.
            print(f"  [scripted ] parameter              {p['Name']}"
                  "  (scripts/set_stage_config.py)")

    print("\n=== ECR repositories ===")
    for r in s.client("ecr").describe_repositories()["repositories"]:
        if r["repositoryName"].startswith(PREFIX_S):
            report("ecr repo", r["repositoryName"], r["repositoryName"] in owned)

    print("\n=== CodeBuild projects ===")
    for n in s.client("codebuild").list_projects()["projects"]:
        if n.startswith(PREFIX_S):
            report("codebuild", n, n in owned)

    print("\n=== state machines / trails / topics ===")
    for sm in s.client("stepfunctions").list_state_machines()["stateMachines"]:
        if sm["name"].startswith(PREFIX_S):
            report("state machine", sm["name"], sm["stateMachineArn"] in owned)
    for t in s.client("cloudtrail").describe_trails()["trailList"]:
        # CloudFormation's physical id for a trail is its name, not its ARN.
        if t["Name"].startswith(PREFIX_S):
            report("cloudtrail", t["Name"],
                   t["Name"] in owned or t.get("TrailARN") in owned)
    for t in s.client("sns").list_topics()["Topics"]:
        name = t["TopicArn"].rsplit(":", 1)[-1]
        if name.startswith(PREFIX_S):
            report("sns topic", name, t["TopicArn"] in owned)

    print()
    if drift:
        print(f"DRIFT: {len(drift)} unmanaged resource(s)")
        for d in drift:
            print(f"  - {d}")
        return 1
    print("NO DRIFT - every project resource is CloudFormation-managed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

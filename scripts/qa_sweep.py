"""Read-only health sweep over everything this project deploys.

    python scripts/qa_sweep.py --profile intelligence-dev

Checks the properties that are easy to break silently: a stack half-updated,
an auth redirect that stopped redirecting, a data layer that is empty, a
state machine whose deployed definition drifted from the template, and any
resource left billing while idle.

Nothing here mutates anything. Exit status is non-zero if a check fails, so
it can gate a deploy.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request

import boto3

PREFIX = "intelligence-engine-"

failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    if not passed:
        failures.append(label)
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urlopen follows redirects by default, which turns a 302 to the login
    page into a 200 and makes an authenticated app look wide open. This is a
    real trap: the naive check passes on a console with auth removed."""

    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--env", default="dev")
    args = ap.parse_args()

    kw = {"region_name": args.region}
    if args.profile:
        kw["profile_name"] = args.profile
    s = boto3.Session(**kw)
    prefix = f"{PREFIX}{args.env}"

    def out(suffix: str, key: str):
        for st in stacks:
            if st["StackName"] == f"{prefix}-{suffix}":
                for o in st.get("Outputs", []):
                    if o["OutputKey"] == key:
                        return o["OutputValue"]
        return None

    print("=== CloudFormation stacks ===")
    stacks = [st for st in s.client("cloudformation").describe_stacks()["Stacks"]
              if st["StackName"].startswith(prefix)]
    bad = [st["StackName"] for st in stacks if not st["StackStatus"].endswith("_COMPLETE")]
    check(f"{len(stacks)} stacks, all COMPLETE", not bad, ", ".join(bad))

    print("\n=== hosted console ===")
    url = out("app", "ServiceUrl")
    if not url:
        print("  (app stack not deployed - skipping)")
    else:
        try:
            code = urllib.request.urlopen(url + "/healthz", timeout=25).getcode()
            check("healthz 200", code == 200, str(code))
        except Exception as e:  # noqa: BLE001
            check("healthz 200", False, type(e).__name__)
        opener = urllib.request.build_opener(_NoRedirect)
        for path in ("/", "/engineer"):
            try:
                got = opener.open(url + path, timeout=25).getcode()
            except urllib.error.HTTPError as e:
                got = e.code
            except Exception as e:  # noqa: BLE001
                got = type(e).__name__
            check(f"{path} gated behind auth", got in (301, 302, 303, 307), f"status {got}")

    print("\n=== data plane ===")
    s3 = s.client("s3")
    lake = out("dataplane", "LakehouseBucketName")
    if lake:
        # Every medallion tier, not just the one that happens to be populated.
        for tier in ("foundational", "derived", "contextualized", "stewardship"):
            n = s3.list_objects_v2(Bucket=lake, Prefix=f"{tier}/").get("KeyCount", 0)
            check(f"{tier} tier populated", n > 0, f"{n} object(s)")
    arts = out("dataplane", "ArtifactsBucketName")
    if arts:
        a = s3.list_objects_v2(Bucket=arts, Prefix="analysis/").get("KeyCount", 0)
        check("analysis artifacts present", a > 0, f"{a} object(s)")
    glue = s.client("glue")
    for db_key, table in (("FoundationalDatabaseName", "profiles"),
                          ("DerivedDatabaseName", "workforce_composition"),
                          ("ContextualizedDatabaseName", "profile_vectors"),
                          ("ContextualizedDatabaseName", "graph_edges")):
        db = out("governance", db_key)
        if not db:
            continue
        try:
            t = glue.get_table(DatabaseName=db, Name=table)["Table"]
            owner = t.get("Parameters", {}).get("ie.owner", "")
            lineage = t.get("Parameters", {}).get("ie.lineage.source_table", "")
            note = "owner+lineage" if (owner and lineage) else "registered"
            check(f"{db}.{table}", True, note)
        except Exception as e:  # noqa: BLE001
            check(f"{db}.{table}", False, type(e).__name__)

    print("\n=== workflow harness ===")
    arn = out("workflow", "StateMachineArn")
    if arn:
        d = s.client("stepfunctions").describe_state_machine(stateMachineArn=arn)
        states = json.loads(d["definition"])["States"]
        check("revision loop present",
              "MidpointDecision" in states and "ReviseMidpointStages" in states)
        check("executions pin an image digest",
              '"ImageOverride.$": "$.stage_image"' in d["definition"])

    print("\n=== resources that bill while idle ===")
    ec2 = s.client("ec2")
    running = [i["InstanceId"]
               for r in ec2.describe_instances(
                   Filters=[{"Name": "instance-state-name", "Values": ["running"]}])["Reservations"]
               for i in r["Instances"]]
    check("no EC2 instance left running", not running, ", ".join(running))
    ngw = ec2.describe_nat_gateways(
        Filter=[{"Name": "state", "Values": ["available"]}])["NatGateways"]
    check("no NAT gateway", not ngw, f"{len(ngw)} found")

    print("\n=== spend ===")
    try:
        today = datetime.date.today()
        r = s.client("ce", region_name="us-east-1").get_cost_and_usage(
            TimePeriod={"Start": today.replace(day=1).isoformat(), "End": today.isoformat()},
            Granularity="MONTHLY", Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
        total, rows = 0.0, []
        for g in r["ResultsByTime"][0]["Groups"]:
            amt = float(g["Metrics"]["UnblendedCost"]["Amount"])
            total += amt
            if amt >= 0.01:
                rows.append((amt, g["Keys"][0]))
        print(f"  month to date: ${total:.2f}")
        for amt, name in sorted(rows, reverse=True)[:10]:
            print(f"    ${amt:>7.2f}  {name}")
    except Exception as e:  # noqa: BLE001
        # Cost Explorer must be enabled in the billing console and takes ~24h
        # to ingest. That is a billing-configuration change this project does
        # not make on its own, so this is expected, not a failure.
        print(f"  unavailable ({type(e).__name__}) - Cost Explorer not enabled.")
        print("  Inference cost is still visible via scripts/bedrock_usage.py")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) - " + "; ".join(failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

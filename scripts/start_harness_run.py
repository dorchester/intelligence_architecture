"""Start a harness (report-build) execution against the blessed or a candidate image.

    # the blessed pipeline (what consultants get):
    python scripts/start_harness_run.py --client sterling-pharma

    # a candidate you just built (FDE testing a variant):
    python scripts/start_harness_run.py --client sterling-pharma \
        --stage-image <repo-uri>@sha256:...

Every execution input must pin stage_image - the state machine threads it
through every stage task - so "which pipeline version ran" is never
ambiguous. This script makes the resolution explicit: --stage-image wins;
otherwise the blessed digest from SSM is used and printed, so a test run
against the default is still a deliberate, visible choice.

Runs from the workbench (the seat holds states:StartExecution on project
state machines and ssm:GetParameter on the project path).
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

import boto3

PARAM = "/intelligence-engine/{env}/stages/blessed-image"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--env", default="dev")
    ap.add_argument("--client", required=True, help="client_id, e.g. sterling-pharma")
    ap.add_argument("--run-id", default=None, help="defaults to r-<timestamp>")
    ap.add_argument("--stage-image", default=None,
                    help="image URI pinned by digest; omit to use the blessed image")
    ap.add_argument("--max-revisions", type=int, default=2)
    args = ap.parse_args()

    kw = {"region_name": args.region}
    if args.profile:
        kw["profile_name"] = args.profile
    s = boto3.Session(**kw)

    image = args.stage_image
    if not image:
        image = s.client("ssm").get_parameter(
            Name=PARAM.format(env=args.env))["Parameter"]["Value"]
        print(f"using blessed image: {image}")
    elif "@sha256:" not in image:
        sys.exit("--stage-image must be pinned by digest (repo-uri@sha256:...), not a tag")

    cfn = s.client("cloudformation")
    arn = next(o["OutputValue"] for o in cfn.describe_stacks(
        StackName=f"intelligence-engine-{args.env}-workflow")["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "StateMachineArn")

    run_id = args.run_id or f"r-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}"
    execution = s.client("stepfunctions").start_execution(
        stateMachineArn=arn,
        name=run_id,
        input=json.dumps({
            "run_id": run_id,
            "client_id": args.client,
            "stage_image": image,
            "max_revisions": args.max_revisions,
        }))
    print(f"started: {run_id}")
    print(f"  execution: {execution['executionArn']}")
    print(f"  watch:     aws stepfunctions describe-execution --execution-arn {execution['executionArn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

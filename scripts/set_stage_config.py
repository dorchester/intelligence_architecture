"""Write the stage configuration parameter that every stage resolves from.

    python scripts/set_stage_config.py --profile intelligence-dev

Stages read one enumerated SSM path rather than hardcoded resource names, so
the same code runs locally, in CodeBuild, and in a different account. That
makes this parameter load-bearing: without it a rebuilt account has stages
that cannot resolve anything.

Every value is read from CloudFormation outputs, so no account identifier
appears in this file or in anything it writes to the repository.

Note on the shell: this is a script rather than a CLI one-liner because
Windows argument parsing strips the double quotes out of embedded JSON, which
produced a parameter that looked correct and failed to parse inside the
container. Writing through boto3 removes the shell from the path entirely.
"""
from __future__ import annotations

import argparse
import json
import sys

import boto3

PARAM = "/intelligence-engine/{env}/stages/demo-config"


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
    cfn, ssm, bedrock = s.client("cloudformation"), s.client("ssm"), s.client("bedrock")
    stack = f"intelligence-engine-{args.env}"

    def out(suffix: str, key: str) -> str:
        r = cfn.describe_stacks(StackName=f"{stack}-{suffix}")["Stacks"][0]
        for o in r.get("Outputs", []):
            if o["OutputKey"] == key:
                return o["OutputValue"]
        raise SystemExit(f"missing output {key} on {stack}-{suffix}")

    def profile_arn(kind: str) -> str:
        want = f"{stack}-client-demo-{kind}"
        for p in bedrock.list_inference_profiles(
                typeEquals="APPLICATION")["inferenceProfileSummaries"]:
            if p["inferenceProfileName"] == want:
                return p["inferenceProfileArn"]
        raise SystemExit(f"no application inference profile named {want}")

    value = {
        # Invoke through the profile ARN, never the bare model id - that is
        # what makes per-client cost attribution work.
        "model_profile_arn": profile_arn("haiku"),
        "sonnet_profile_arn": profile_arn("sonnet"),
        "embedding_model_id": "amazon.titan-embed-text-v2:0",

        "source_bucket": out("storage", "BucketName"),
        "landing_bucket": out("dataplane", "LandingBucketName"),
        "lakehouse_bucket": out("dataplane", "LakehouseBucketName"),
        "artifacts_bucket": out("dataplane", "ArtifactsBucketName"),
        "athena_workgroup": out("dataplane", "AthenaWorkGroupName"),

        # Medallion tiers. Stages name a tier, never a raw prefix.
        "foundational_db": out("governance", "FoundationalDatabaseName"),
        "derived_db": out("governance", "DerivedDatabaseName"),
        "contextualized_db": out("governance", "ContextualizedDatabaseName"),
        "stewardship_db": out("governance", "StewardshipDatabaseName"),
        "steward_topic_arn": out("governance", "StewardTopicArn"),
    }

    name = PARAM.format(env=args.env)
    version = ssm.put_parameter(Name=name, Value=json.dumps(value),
                                Type="SecureString", Overwrite=True)["Version"]

    back = json.loads(ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"])
    if back != value:
        sys.exit("round-trip mismatch - the parameter did not store what was sent")

    print(f"wrote {name} version {version}")
    print(f"keys: {', '.join(sorted(back))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

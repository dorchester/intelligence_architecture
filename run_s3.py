"""S3-backed runner.

Executes the workflow with artifacts stored in S3, demonstrating the full
AWS-integrated thin slice.
"""

import argparse
from pathlib import Path

from agent.agent import run_thin_slice
from agent.context import RunContext
from agent.model import BedrockModel, ModelConfig
from storage.s3 import S3Storage


def get_bucket_name(profile: str, region: str) -> str:
    """Retrieve bucket name from CloudFormation stack outputs."""
    import boto3

    session = boto3.Session(profile_name=profile, region_name=region)
    cfn = session.client("cloudformation")
    response = cfn.describe_stacks(StackName="intelligence-engine-dev-storage")
    outputs = response["Stacks"][0]["Outputs"]
    for output in outputs:
        if output["OutputKey"] == "BucketName":
            return output["OutputValue"]
    raise RuntimeError("BucketName not found in stack outputs")


def main():
    parser = argparse.ArgumentParser(description="Intelligence Engine S3 runner")
    parser.add_argument("--profile", default="intelligence-dev", help="AWS profile name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--model", default="us.anthropic.claude-sonnet-4-6", help="Bedrock model ID")
    parser.add_argument("--client-id", default="client-001", help="Client identifier")
    parser.add_argument("--client-name", default="Meridian Dynamics", help="Client display name")
    parser.add_argument("--no-bedrock", action="store_true", help="Use stub narrative (skip Bedrock)")
    args = parser.parse_args()

    bucket = get_bucket_name(args.profile, args.region)
    storage = S3Storage(bucket=bucket, region=args.region, profile=args.profile)

    ctx = RunContext(
        client_id=args.client_id,
        client_name=args.client_name,
        storage=storage,
    )

    model = None
    if not args.no_bedrock:
        config = ModelConfig(model_id=args.model, profile=args.profile, region=args.region)
        model = BedrockModel(config)

    input_csv = Path("sample_data/fictional_workforce.csv")

    print(f"Starting S3-backed run")
    print(f"  Run ID:  {ctx.run_id}")
    print(f"  Client:  {ctx.client_name} ({ctx.client_id})")
    print(f"  Bucket:  {bucket}")
    print(f"  Model:   {model.model_id if model else 'stub'}")
    print()

    output_path = run_thin_slice(ctx, input_csv, model=model)

    print(f"Run complete.")
    print(f"  Stage:   {ctx.stage.value}")
    print(f"  Output:  {output_path}")
    print(f"\nArtifacts in S3:")
    for category in ("input", "working", "output"):
        files = ctx.list_artifacts(category)
        for f in files:
            print(f"  {category}/{f}")


if __name__ == "__main__":
    main()

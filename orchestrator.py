"""Full orchestrator: integrates S3 storage, DynamoDB state, Bedrock model, and checkpoints.

This is the V0 operator-facing entry point for running the Intelligence Engine
on AWS infrastructure. The agent reasons through the methodology playbook,
invokes deterministic tools, and produces a report.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import boto3

from agent.context import RunContext, Stage
from agent.engine import AgentEngine
from agent.logging_config import configure_logging
from agent.model import BedrockModel, ModelConfig
from state.run_state import RunStateManager
from storage.s3 import S3Storage

logger = logging.getLogger("intelligence_engine.orchestrator")


def get_stack_output(session, stack_name: str, output_key: str) -> str:
    cfn = session.client("cloudformation")
    response = cfn.describe_stacks(StackName=stack_name)
    for output in response["Stacks"][0]["Outputs"]:
        if output["OutputKey"] == output_key:
            return output["OutputValue"]
    raise RuntimeError(f"{output_key} not found in {stack_name}")


def run_with_checkpoint(args):
    """Execute the full agent workflow with a checkpoint approval gate."""
    session = boto3.Session(profile_name=args.profile, region_name=args.region)

    bucket = get_stack_output(session, "intelligence-engine-dev-storage", "BucketName")
    table = get_stack_output(session, "intelligence-engine-dev-state", "TableName")

    storage = S3Storage(bucket=bucket, region=args.region, profile=args.profile)
    state_mgr = RunStateManager(table_name=table, region=args.region, profile=args.profile)
    model_config = ModelConfig(model_id=args.model, profile=args.profile, region=args.region)
    model = BedrockModel(model_config)

    ctx = RunContext(
        client_id=args.client_id,
        client_name=args.client_name,
        storage=storage,
    )
    ctx.model_id = model.model_id

    # Register run
    state_mgr.create_run(
        run_id=ctx.run_id,
        client_id=ctx.client_id,
        client_name=ctx.client_name,
        model_id=model.model_id,
    )

    print(f"=== Intelligence Engine Run ===")
    print(f"  Run ID:   {ctx.run_id}")
    print(f"  Client:   {ctx.client_name} ({ctx.client_id})")
    print(f"  Bucket:   {bucket}")
    print(f"  Model:    {model.model_id}")
    print()

    # Load input to S3
    input_csv = Path("sample_data/fictional_workforce.csv")
    ctx.write_artifact("input", input_csv.name, input_csv.read_bytes())
    ctx.advance_to(Stage.DATA_LOADED)
    state_mgr.update_stage(ctx.run_id, "data_loaded")
    logger.info("Input loaded to S3", extra={"run_id": ctx.run_id})
    print("[1/4] Input loaded to S3")

    # Checkpoint before agent execution
    state_mgr.request_approval(
        ctx.run_id,
        checkpoint_name="pre_execution",
        description="Input loaded. Approve to proceed with agent-driven analysis and report generation.",
    )
    ctx.advance_to(Stage.WAITING_FOR_APPROVAL)
    print()
    print(f"  ** CHECKPOINT: pre_execution **")
    print(f"  Input file: {input_csv.name}")
    print()

    if args.auto_approve:
        print("  [auto-approving checkpoint]")
        state_mgr.approve_checkpoint(ctx.run_id, approved_by="auto")
    else:
        input("  Press ENTER to approve and continue (or Ctrl+C to abort)...")
        state_mgr.approve_checkpoint(ctx.run_id, approved_by="operator")

    print()
    state_mgr.update_stage(ctx.run_id, "running")

    # Agent-driven execution
    print("[2/4] Agent reasoning through methodology...")
    start = time.time()
    engine = AgentEngine(ctx, model)
    output_path = engine.execute(methodology_file="thin_slice.md")
    elapsed = time.time() - start
    print(f"[3/4] Agent complete ({elapsed:.1f}s)")

    # Finalize
    state_mgr.update_stage(ctx.run_id, "report_generated")
    state_mgr.complete_run(ctx.run_id, output_path)
    ctx.advance_to(Stage.COMPLETED)
    print(f"[4/4] Run finalized")

    print()
    print(f"=== Run Complete ===")
    print(f"  Output:  {output_path}")
    print()

    # Final state
    run_record = state_mgr.get_run(ctx.run_id)
    print(f"  DynamoDB state:")
    print(f"    stage:       {run_record['stage']}")
    print(f"    created_at:  {run_record['created_at']}")
    print(f"    completed:   {run_record.get('completed_at', 'N/A')}")
    print(f"    checkpoints: {len(run_record.get('checkpoints', []))}")
    print()
    print(f"  S3 artifacts:")
    for category in ("input", "working", "output"):
        files = ctx.list_artifacts(category)
        for f in files:
            print(f"    {category}/{f}")

    return ctx.run_id


def check_status(args):
    """Check the status of a run."""
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = get_stack_output(session, "intelligence-engine-dev-state", "TableName")
    state_mgr = RunStateManager(table_name=table, region=args.region, profile=args.profile)

    run = state_mgr.get_run(args.run_id)
    if not run:
        print(f"Run {args.run_id} not found.")
        return

    print(f"Run: {run['run_id']}")
    print(f"  Client:       {run['client_name']} ({run['client_id']})")
    print(f"  Stage:        {run['stage']}")
    print(f"  Model:        {run.get('model_id', 'N/A')}")
    print(f"  Created:      {run['created_at']}")
    print(f"  Updated:      {run['updated_at']}")
    if run.get("output_location"):
        print(f"  Output:       {run['output_location']}")
    if run.get("current_checkpoint") and run["current_checkpoint"]:
        cp = run["current_checkpoint"]
        print(f"  Checkpoint:   {cp.get('name')} - {cp.get('status')}")
    print(f"  Checkpoints:  {len(run.get('checkpoints', []))}")


def download_report(args):
    """Download a run's report from S3."""
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    bucket = get_stack_output(session, "intelligence-engine-dev-storage", "BucketName")
    table = get_stack_output(session, "intelligence-engine-dev-state", "TableName")

    state_mgr = RunStateManager(table_name=table, region=args.region, profile=args.profile)
    run = state_mgr.get_run(args.run_id)
    if not run:
        print(f"Run {args.run_id} not found.")
        return

    storage = S3Storage(bucket=bucket, region=args.region, profile=args.profile)
    try:
        report_data = storage.read(args.run_id, run["client_id"], "output", "report.html")
    except Exception as e:
        print(f"Error reading report: {e}")
        return

    out_file = Path(args.output or f"report_{args.run_id[:8]}.html")
    out_file.write_bytes(report_data)
    print(f"Report downloaded to: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Intelligence Engine Orchestrator")
    parser.add_argument("--profile", default="intelligence-dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-file", default=None, help="Path to JSON log file")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Start a new run")
    run_parser.add_argument("--model", default="us.anthropic.claude-sonnet-4-6")
    run_parser.add_argument("--client-id", default="client-001")
    run_parser.add_argument("--client-name", default="Meridian Dynamics")
    run_parser.add_argument("--auto-approve", action="store_true")

    status_parser = subparsers.add_parser("status", help="Check run status")
    status_parser.add_argument("run_id")

    dl_parser = subparsers.add_parser("download", help="Download a run's report")
    dl_parser.add_argument("run_id")
    dl_parser.add_argument("--output", "-o", default=None)

    args = parser.parse_args()

    configure_logging(level=args.log_level, log_file=args.log_file)

    if args.command == "run":
        run_with_checkpoint(args)
    elif args.command == "status":
        check_status(args)
    elif args.command == "download":
        download_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

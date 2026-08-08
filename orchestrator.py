"""Full orchestrator: integrates S3 storage, DynamoDB state, Bedrock model, and checkpoints.

This is the V0 operator-facing entry point for running the Intelligence Engine
on AWS infrastructure.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import boto3

from agent.agent import run_thin_slice
from agent.context import RunContext, Stage
from agent.model import BedrockModel, ModelConfig
from state.run_state import RunStateManager
from storage.s3 import S3Storage


def get_stack_output(session, stack_name: str, output_key: str) -> str:
    cfn = session.client("cloudformation")
    response = cfn.describe_stacks(StackName=stack_name)
    for output in response["Stacks"][0]["Outputs"]:
        if output["OutputKey"] == output_key:
            return output["OutputValue"]
    raise RuntimeError(f"{output_key} not found in {stack_name}")


def run_with_checkpoint(args):
    """Execute the full workflow with a checkpoint approval gate."""
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

    # Register run in state store
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
    print(f"  Table:    {table}")
    print(f"  Model:    {model.model_id}")
    print()

    # Execute the workflow
    input_csv = Path("sample_data/fictional_workforce.csv")

    # Phase 1: Load and analyze
    state_mgr.update_stage(ctx.run_id, "running")
    csv_data = input_csv.read_bytes()
    ctx.write_artifact("input", input_csv.name, csv_data)
    ctx.advance_to(Stage.DATA_LOADED)
    state_mgr.update_stage(ctx.run_id, "data_loaded")
    print("[1/5] Data loaded to S3")

    from tools.basic_analysis import run_workforce_analysis
    metrics = run_workforce_analysis(input_csv)
    ctx.advance_to(Stage.ANALYSIS_COMPLETE)
    state_mgr.update_stage(ctx.run_id, "analysis_complete")
    print("[2/5] Analysis complete")

    # Phase 2: Generate chart
    import tempfile
    from tools.chart import generate_headcount_chart
    with tempfile.TemporaryDirectory() as tmp:
        chart_path = generate_headcount_chart(metrics, Path(tmp))
        chart_data = chart_path.read_bytes()
    ctx.write_artifact("working", "headcount_by_department.png", chart_data)
    print("[3/5] Chart generated")

    # Checkpoint: approve before narrative generation
    state_mgr.request_approval(
        ctx.run_id,
        checkpoint_name="pre_narrative",
        description="Analysis complete. Approve to proceed with narrative generation.",
    )
    ctx.advance_to(Stage.WAITING_FOR_APPROVAL)
    print()
    print(f"  ** CHECKPOINT: pre_narrative **")
    print(f"  Analysis metrics: {json.dumps({k: v for k, v in metrics.items() if k != 'department_breakdown'}, indent=2)}")
    print()

    if args.auto_approve:
        print("  [auto-approving checkpoint]")
        state_mgr.approve_checkpoint(ctx.run_id, approved_by="auto")
    else:
        input("  Press ENTER to approve and continue (or Ctrl+C to abort)...")
        state_mgr.approve_checkpoint(ctx.run_id, approved_by="operator")

    state_mgr.update_stage(ctx.run_id, "running")
    print()

    # Phase 3: Generate narrative
    from agent.agent import _generate_narrative, SYSTEM_PROMPT, NARRATIVE_TEMPLATE
    narrative = _generate_narrative(ctx, metrics, model)
    ctx.model_id = model.model_id
    ctx.write_artifact("working", "narrative.txt", narrative.encode("utf-8"))
    ctx.advance_to(Stage.NARRATIVE_COMPLETE)
    state_mgr.update_stage(ctx.run_id, "narrative_complete")
    print("[4/5] Narrative generated")

    # Phase 4: Render report
    from tools.report import render_report
    report_html = render_report(ctx, metrics, narrative, chart_data)
    output_location = ctx.write_artifact("output", "report.html", report_html.encode("utf-8"))
    ctx.advance_to(Stage.REPORT_GENERATED)
    state_mgr.update_stage(ctx.run_id, "report_generated")
    print("[5/5] Report rendered")

    # Mark complete
    state_mgr.complete_run(ctx.run_id, output_location)
    ctx.advance_to(Stage.COMPLETED)

    print()
    print(f"=== Run Complete ===")
    print(f"  Output:  {output_location}")
    print()

    # Show final state
    run_record = state_mgr.get_run(ctx.run_id)
    print(f"  DynamoDB state:")
    print(f"    stage:      {run_record['stage']}")
    print(f"    created_at: {run_record['created_at']}")
    print(f"    completed:  {run_record.get('completed_at', 'N/A')}")
    print(f"    checkpoints: {len(run_record.get('checkpoints', []))}")

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
    if run.get("current_checkpoint"):
        cp = run["current_checkpoint"]
        print(f"  Checkpoint:   {cp.get('name')} - {cp.get('status')}")
    print(f"  Checkpoints:  {len(run.get('checkpoints', []))}")


def main():
    parser = argparse.ArgumentParser(description="Intelligence Engine Orchestrator")
    parser.add_argument("--profile", default="intelligence-dev")
    parser.add_argument("--region", default="us-east-1")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Start a new run")
    run_parser.add_argument("--model", default="us.anthropic.claude-sonnet-4-6")
    run_parser.add_argument("--client-id", default="client-001")
    run_parser.add_argument("--client-name", default="Meridian Dynamics")
    run_parser.add_argument("--auto-approve", action="store_true", help="Auto-approve checkpoints")

    status_parser = subparsers.add_parser("status", help="Check run status")
    status_parser.add_argument("run_id", help="Run ID to check")

    args = parser.parse_args()

    if args.command == "run":
        run_with_checkpoint(args)
    elif args.command == "status":
        check_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

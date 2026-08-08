"""Local runner.

Executes the V0 workflow with synthetic data.
Supports both local-only mode (no AWS) and Bedrock-enabled mode.
"""

import argparse
from pathlib import Path

from agent.agent import run_thin_slice
from agent.context import RunContext
from agent.model import BedrockModel, ModelConfig
from storage.local import LocalStorage


def main():
    parser = argparse.ArgumentParser(description="Intelligence Engine local runner")
    parser.add_argument(
        "--use-bedrock", action="store_true", help="Use Bedrock for narrative generation"
    )
    parser.add_argument("--profile", default="intelligence-dev", help="AWS profile name")
    parser.add_argument("--model", default="us.anthropic.claude-sonnet-4-6", help="Bedrock model ID")
    args = parser.parse_args()

    storage = LocalStorage(base_dir=Path("runs"))
    ctx = RunContext(
        client_id="client-001",
        client_name="Meridian Dynamics",
        storage=storage,
    )

    model = None
    if args.use_bedrock:
        config = ModelConfig(model_id=args.model, profile=args.profile)
        model = BedrockModel(config)

    input_csv = Path("sample_data/fictional_workforce.csv")

    print(f"Starting run")
    print(f"  Run ID:  {ctx.run_id}")
    print(f"  Client:  {ctx.client_name}")
    print(f"  Model:   {model.model_id if model else 'stub'}")
    print()

    output_path = run_thin_slice(ctx, input_csv, model=model)

    print(f"Run complete.")
    print(f"  Stage:   {ctx.stage.value}")
    print(f"  Output:  {output_path}")


if __name__ == "__main__":
    main()

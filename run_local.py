"""Local thin-slice runner.

Executes the V0 workflow entirely locally with synthetic data.
No AWS calls are made.
"""

from pathlib import Path

from agent.agent import run_thin_slice
from agent.context import RunContext


def main():
    ctx = RunContext(
        client_id="client-001",
        client_name="Meridian Dynamics",
    )

    input_csv = Path("sample_data/fictional_workforce.csv")

    print(f"Starting thin-slice run")
    print(f"  Run ID:  {ctx.run_id}")
    print(f"  Client:  {ctx.client_name}")
    print(f"  Input:   {input_csv}")
    print()

    report_path = run_thin_slice(ctx, input_csv)

    print(f"Run complete.")
    print(f"  Stage:   {ctx.stage.value}")
    print(f"  Report:  {report_path}")
    print(f"\nOpen the report in a browser to view results.")


if __name__ == "__main__":
    main()

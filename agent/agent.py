"""Thin-slice agent orchestrator.

In V0 this is a simple sequential runner that demonstrates the architectural
separation between deterministic tools and LLM-driven narrative. The LLM
narrative step is stubbed with a placeholder until Bedrock integration is wired.
"""

from __future__ import annotations

from pathlib import Path

from agent.context import RunContext, Stage
from tools.basic_analysis import run_workforce_analysis
from tools.chart import generate_headcount_chart
from tools.report import render_report


def run_thin_slice(ctx: RunContext, input_csv: Path) -> Path:
    """Execute the V0 thin-slice workflow and return the path to the final report."""

    ctx.ensure_directories()

    # Copy input to run's input directory
    import shutil
    dest = ctx.input_path / input_csv.name
    shutil.copy2(input_csv, dest)
    ctx.advance_to(Stage.DATA_LOADED)

    # Deterministic analysis
    metrics = run_workforce_analysis(dest)
    ctx.advance_to(Stage.ANALYSIS_COMPLETE)

    # Generate chart
    chart_path = generate_headcount_chart(metrics, ctx.working_path)

    # Narrative (stubbed — will be replaced by Bedrock LLM call)
    narrative = _stub_narrative(ctx, metrics)
    ctx.advance_to(Stage.NARRATIVE_COMPLETE)

    # Render report
    report_path = render_report(ctx, metrics, narrative, chart_path)
    ctx.advance_to(Stage.REPORT_GENERATED)

    return report_path


def _stub_narrative(ctx: RunContext, metrics: dict) -> str:
    """Placeholder narrative until Bedrock integration is active."""
    total = metrics["total_headcount"]
    departments = metrics["department_count"]
    return (
        f"This analysis covers {total} employees across {departments} departments "
        f"for {ctx.client_name}. The workforce distribution shows concentration in "
        f"the largest department ({metrics['largest_department']}) which represents "
        f"{metrics['largest_department_pct']:.1f}% of total headcount. "
        f"Average tenure is {metrics['avg_tenure_years']:.1f} years."
    )

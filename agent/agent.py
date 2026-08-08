"""Agent orchestrator.

Orchestrates the analytical workflow: loads data, runs deterministic tools,
invokes the LLM for narrative, and renders the final report.
"""

from __future__ import annotations

from pathlib import Path

from agent.context import RunContext, Stage
from agent.model import BedrockModel
from tools.basic_analysis import run_workforce_analysis
from tools.chart import generate_headcount_chart
from tools.report import render_report


SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "system.md").read_text()
NARRATIVE_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "prompts" / "report_narrative.md"
).read_text()


def run_thin_slice(
    ctx: RunContext,
    input_csv: Path,
    model: BedrockModel | None = None,
) -> str:
    """Execute the V0 thin-slice workflow. Returns the output path/URI of the report."""

    # Store input
    csv_data = input_csv.read_bytes()
    ctx.write_artifact("input", input_csv.name, csv_data)
    ctx.advance_to(Stage.DATA_LOADED)

    # Deterministic analysis
    metrics = run_workforce_analysis(input_csv)
    ctx.advance_to(Stage.ANALYSIS_COMPLETE)

    # Generate chart to a temp path then persist via storage
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        chart_path = generate_headcount_chart(metrics, tmp_path)
        chart_data = chart_path.read_bytes()
    ctx.write_artifact("working", "headcount_by_department.png", chart_data)

    # Narrative generation
    if model:
        narrative = _generate_narrative(ctx, metrics, model)
        ctx.model_id = model.model_id
    else:
        narrative = _stub_narrative(ctx, metrics)
    ctx.write_artifact("working", "narrative.txt", narrative.encode("utf-8"))
    ctx.advance_to(Stage.NARRATIVE_COMPLETE)

    # Render report
    report_html = render_report(ctx, metrics, narrative, chart_data)
    output_path = ctx.write_artifact("output", "report.html", report_html.encode("utf-8"))
    ctx.advance_to(Stage.REPORT_GENERATED)

    return output_path


def _generate_narrative(ctx: RunContext, metrics: dict, model: BedrockModel) -> str:
    """Generate narrative interpretation using Bedrock."""
    prompt = NARRATIVE_TEMPLATE.replace("{{client_name}}", ctx.client_name)
    prompt = prompt.replace("{{total_headcount}}", str(metrics["total_headcount"]))
    prompt = prompt.replace("{{department_count}}", str(metrics["department_count"]))
    prompt = prompt.replace("{{largest_department}}", metrics["largest_department"])
    prompt = prompt.replace(
        "{{largest_department_pct}}", f"{metrics['largest_department_pct']:.1f}"
    )
    prompt = prompt.replace("{{avg_tenure_years}}", f"{metrics['avg_tenure_years']:.1f}")
    prompt = prompt.replace("{{median_tenure_years}}", f"{metrics['median_tenure_years']:.1f}")
    prompt = prompt.replace(
        "{{turnover_risk_high_pct}}", f"{metrics['turnover_risk_high_pct']:.1f}"
    )

    return model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system=SYSTEM_PROMPT,
    )


def _stub_narrative(ctx: RunContext, metrics: dict) -> str:
    """Placeholder narrative when no model is available."""
    total = metrics["total_headcount"]
    departments = metrics["department_count"]
    return (
        f"This analysis covers {total} employees across {departments} departments "
        f"for {ctx.client_name}. The workforce distribution shows concentration in "
        f"the largest department ({metrics['largest_department']}) which represents "
        f"{metrics['largest_department_pct']:.1f}% of total headcount. "
        f"Average tenure is {metrics['avg_tenure_years']:.1f} years."
    )

"""Report generation tool.

Renders the final HTML report from a Jinja2 template, metrics, and narrative.
"""

from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from agent.context import RunContext

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def render_report(
    ctx: RunContext,
    metrics: dict,
    narrative: str,
    chart_path: Path,
) -> Path:
    """Render the HTML report and write it to the run's output directory."""

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    chart_b64 = base64.b64encode(chart_path.read_bytes()).decode("utf-8")

    html = template.render(
        client_name=ctx.client_name,
        run_id=ctx.run_id,
        created_at=ctx.created_at,
        methodology_version=ctx.methodology_version,
        code_version=ctx.code_version,
        metrics=metrics,
        narrative=narrative,
        chart_b64=chart_b64,
    )

    report_path = ctx.output_path / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path

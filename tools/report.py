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
    chart_png_bytes: bytes,
) -> str:
    """Render the HTML report and return it as a string."""

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    chart_b64 = base64.b64encode(chart_png_bytes).decode("utf-8")

    html = template.render(
        client_name=ctx.client_name,
        run_id=ctx.run_id,
        created_at=ctx.created_at,
        methodology_version=ctx.methodology_version,
        code_version=ctx.code_version,
        model_id=ctx.model_id or "stub",
        metrics=metrics,
        narrative=narrative,
        chart_b64=chart_b64,
    )

    return html

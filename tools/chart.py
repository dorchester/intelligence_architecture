"""Chart generation tools.

Deterministic chart generation from analysis metrics.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def generate_headcount_chart(metrics: dict, output_dir: Path) -> Path:
    """Generate a department headcount bar chart and return the file path."""

    departments = metrics["department_breakdown"]
    names = list(departments.keys())
    counts = list(departments.values())

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(names, counts, color="#2563eb")
    ax.set_xlabel("Headcount")
    ax.set_title("Headcount by Department")
    ax.invert_yaxis()
    fig.tight_layout()

    chart_path = output_dir / "headcount_by_department.png"
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)

    return chart_path

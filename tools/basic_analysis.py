"""Deterministic workforce analysis tool.

This module performs purely computational analysis on structured data.
It has no LLM dependency and produces reproducible results given the same input.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def run_workforce_analysis(csv_path: Path) -> dict:
    """Analyze a workforce CSV and return a metrics dictionary."""

    df = pd.read_csv(csv_path)

    total_headcount = len(df)
    department_counts = df["department"].value_counts()
    largest_dept = department_counts.index[0]
    largest_dept_count = department_counts.iloc[0]

    metrics = {
        "total_headcount": total_headcount,
        "department_count": df["department"].nunique(),
        "department_breakdown": department_counts.to_dict(),
        "largest_department": largest_dept,
        "largest_department_headcount": int(largest_dept_count),
        "largest_department_pct": (largest_dept_count / total_headcount) * 100,
        "avg_tenure_years": float(df["tenure_years"].mean()),
        "median_tenure_years": float(df["tenure_years"].median()),
        "avg_age": float(df["age"].mean()),
        "turnover_risk_high_pct": (
            (df["turnover_risk"] == "High").sum() / total_headcount * 100
        ),
    }

    return metrics

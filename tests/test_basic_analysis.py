"""Tests for deterministic workforce analysis."""

from pathlib import Path

import pytest

from tools.basic_analysis import run_workforce_analysis

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "fictional_workforce.csv"


def test_analysis_returns_expected_keys():
    metrics = run_workforce_analysis(SAMPLE_CSV)
    expected_keys = {
        "total_headcount",
        "department_count",
        "department_breakdown",
        "largest_department",
        "largest_department_headcount",
        "largest_department_pct",
        "avg_tenure_years",
        "median_tenure_years",
        "avg_age",
        "turnover_risk_high_pct",
    }
    assert set(metrics.keys()) == expected_keys


def test_total_headcount():
    metrics = run_workforce_analysis(SAMPLE_CSV)
    assert metrics["total_headcount"] == 20


def test_department_count():
    metrics = run_workforce_analysis(SAMPLE_CSV)
    assert metrics["department_count"] == 6


def test_largest_department():
    metrics = run_workforce_analysis(SAMPLE_CSV)
    assert metrics["largest_department"] == "Engineering"
    assert metrics["largest_department_headcount"] == 5


def test_percentages_are_valid():
    metrics = run_workforce_analysis(SAMPLE_CSV)
    assert 0 <= metrics["largest_department_pct"] <= 100
    assert 0 <= metrics["turnover_risk_high_pct"] <= 100


def test_tenure_values_reasonable():
    metrics = run_workforce_analysis(SAMPLE_CSV)
    assert 0 < metrics["avg_tenure_years"] < 50
    assert 0 < metrics["median_tenure_years"] < 50
    assert metrics["median_tenure_years"] <= metrics["avg_tenure_years"]

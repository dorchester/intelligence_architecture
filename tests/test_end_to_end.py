"""End-to-end test for the thin-slice workflow (no AWS calls)."""

from pathlib import Path

import pytest

from agent.agent import run_thin_slice
from agent.context import RunContext, Stage
from storage.local import LocalStorage

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "fictional_workforce.csv"


@pytest.fixture
def run_ctx(tmp_path):
    storage = LocalStorage(base_dir=tmp_path)
    return RunContext(
        client_id="test-client",
        client_name="Test Corp",
        storage=storage,
    )


def test_thin_slice_completes(run_ctx):
    output_path = run_thin_slice(run_ctx, SAMPLE_CSV, model=None)
    assert run_ctx.stage == Stage.REPORT_GENERATED
    assert "report.html" in output_path


def test_thin_slice_produces_artifacts(run_ctx):
    run_thin_slice(run_ctx, SAMPLE_CSV, model=None)
    assert run_ctx.artifact_exists("input", "fictional_workforce.csv")
    assert run_ctx.artifact_exists("working", "headcount_by_department.png")
    assert run_ctx.artifact_exists("working", "narrative.txt")
    assert run_ctx.artifact_exists("output", "report.html")


def test_report_contains_client_name(run_ctx):
    run_thin_slice(run_ctx, SAMPLE_CSV, model=None)
    report = run_ctx.read_artifact("output", "report.html").decode("utf-8")
    assert "Test Corp" in report


def test_report_contains_run_id(run_ctx):
    run_thin_slice(run_ctx, SAMPLE_CSV, model=None)
    report = run_ctx.read_artifact("output", "report.html").decode("utf-8")
    assert run_ctx.run_id in report

"""Durable checkpoint tests.

The property under test: a run parked at WAITING_FOR_APPROVAL holds no thread
and survives a process restart - approval afterwards continues the workflow
exactly where it stopped. LLM calls are stubbed; no AWS is touched.
"""

from __future__ import annotations

import time

import pytest

from webapp import app as webapp_app
from webapp import runtime


class MemoryStore:
    """In-memory stand-in for the DynamoDB store, same interface."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.saves = 0

    def available(self):
        return True

    def save(self, run):
        import json

        self.saves += 1
        clean = {k: v for k, v in run.items() if not k.startswith("_")}
        self.items[run["run_id"]] = json.loads(json.dumps(clean, default=str))

    def load(self, run_id):
        import copy

        item = self.items.get(run_id)
        return copy.deepcopy(item) if item else None

    def load_all(self, limit=50):
        import copy

        return [copy.deepcopy(v) for v in self.items.values()][:limit]


class StubModel:
    model_id = "stub-model"

    def invoke(self, *a, **k):
        return "{}"


@pytest.fixture
def client(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(runtime, "get_run_store", lambda: store)
    monkeypatch.setattr(webapp_app, "get_bedrock_model", lambda *a, **k: StubModel())
    monkeypatch.setattr(runtime, "get_dataset_query", lambda: None)
    monkeypatch.setattr(webapp_app, "get_dataset_query", lambda: None, raising=False)

    # Deterministic phase outputs
    monkeypatch.setattr(webapp_app, "_research_company", lambda *a, **k: {
        "full_name": "Test Corp", "industry": "Testing", "headquarters": "Testville",
        "employee_count": "~1,000", "revenue": "$1B",
        "segments": ["A"], "recent_developments": ["B"],
    })
    monkeypatch.setattr(webapp_app, "_revise_research", lambda m, c, r, f: {
        **r, "employee_count": "~2,000 (revised)",
    })
    monkeypatch.setattr(webapp_app, "_analyze_organization", lambda *a, **k: {
        "structure_summary": "Flat", "workforce_summary": "Small",
        "challenges": [], "workforce_risks": [],
    })
    monkeypatch.setattr(webapp_app, "_identify_opportunities", lambda *a, **k: {
        "opportunities": [{"title": "T", "description": "D", "impact": "I"}],
    })
    monkeypatch.setattr(webapp_app, "_generate_briefing",
                        lambda *a, **k: "A briefing. " * 60)
    monkeypatch.setattr(webapp_app, "_render_intelligence_report",
                        lambda *a, **k: "<html>report</html>")

    # No entity verification (it needs Bedrock) and permissive guardrails
    monkeypatch.setattr(webapp_app.guardrails, "entity_verification_enabled",
                        lambda: False)

    webapp_app.runs.clear()
    with webapp_app.app.test_client() as c:
        yield c, store
    webapp_app.runs.clear()


def _wait_for_stage(run_id, stage, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = webapp_app.runs.get(run_id)
        if run and run["stage"] == stage:
            return run
        time.sleep(0.05)
    raise AssertionError(
        f"run never reached {stage}; at "
        f"{webapp_app.runs.get(run_id, {}).get('stage')}"
    )


def _start(client_):
    resp = client_.post("/run/start", data={
        "company_name": "Meridian Insurance Group", "engagement_context": "",
    })
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "/run/" in location, f"guardrail rejected the start: {location}"
    return location.rstrip("/").split("/")[-1]


def test_run_reaches_first_checkpoint_and_persists(client):
    c, store = client
    run_id = _start(c)
    run = _wait_for_stage(run_id, "waiting_for_approval")
    assert run["current_checkpoint"]["name"] == "research_validation"
    saved = store.load(run_id)
    assert saved is not None
    assert saved["stage"] == "waiting_for_approval"
    assert saved["research"]["full_name"] == "Test Corp"


def test_checkpoint_survives_restart_and_approval_continues(client):
    c, store = client
    run_id = _start(c)
    _wait_for_stage(run_id, "waiting_for_approval")

    # "Restart": the in-memory registry is wiped; only the store remains.
    webapp_app.runs.clear()

    # The run is still reachable, hydrated from the store...
    resp = c.get(f"/run/{run_id}")
    assert resp.status_code == 200

    # ...and approving it continues the workflow into the next phase.
    c.post(f"/run/{run_id}/approve", data={"feedback": ""})
    run = _wait_for_stage(run_id, "waiting_for_approval", timeout=8)
    assert run["current_checkpoint"]["name"] == "org_review"


def test_full_workflow_completes_through_all_checkpoints(client):
    c, store = client
    run_id = _start(c)
    expected = ["research_validation", "org_review", "opportunity_review",
                "briefing_review", "final_review"]
    for name in expected:
        run = _wait_for_stage(run_id, "waiting_for_approval", timeout=8)
        assert run["current_checkpoint"]["name"] == name
        c.post(f"/run/{run_id}/approve", data={"feedback": ""})
    deadline = time.time() + 8
    while time.time() < deadline:
        if webapp_app.runs[run_id]["stage"] == "completed":
            break
        time.sleep(0.05)
    assert webapp_app.runs[run_id]["stage"] == "completed"
    assert store.load(run_id)["stage"] == "completed"
    # Report content is durable even though /tmp is not
    assert store.load(run_id)["report_html"] == "<html>report</html>"


def test_revision_reruns_phase_and_reparks_at_same_checkpoint(client):
    c, store = client
    run_id = _start(c)
    _wait_for_stage(run_id, "waiting_for_approval")

    c.post(f"/run/{run_id}/revise", data={"feedback": "Headcount is wrong"})
    run = _wait_for_stage(run_id, "waiting_for_approval", timeout=8)
    assert run["current_checkpoint"]["name"] == "research_validation"
    assert run["research"]["employee_count"] == "~2,000 (revised)"


def test_interrupted_midphase_run_is_marked_on_hydration(client):
    c, store = client
    run_id = _start(c)
    run = _wait_for_stage(run_id, "waiting_for_approval")

    # Forge a mid-phase snapshot, as if the process died while researching.
    run["stage"] = "researching"
    store.save(run)
    webapp_app.runs.clear()

    resp = c.get(f"/api/run/{run_id}/status")
    assert resp.status_code == 200
    assert resp.get_json()["stage"] == "interrupted"


def test_status_api_omits_bulky_fields(client):
    c, store = client
    run_id = _start(c)
    _wait_for_stage(run_id, "waiting_for_approval")
    data = c.get(f"/api/run/{run_id}/status").get_json()
    assert "report_html" not in data
    assert "dataset_context" not in data

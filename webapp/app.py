"""Consultant-facing web UI for the Intelligence Engine.

Provides a minimal interface for selecting a client, starting a run,
approving checkpoints, and viewing the final report.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CLIENTS_DIR = BASE_DIR / "sample_data" / "clients"
RUNS_DIR = BASE_DIR / "runs"

# In-memory run state (V0 — DynamoDB in production)
runs: dict[str, dict] = {}


def load_clients() -> list[dict]:
    manifest_path = CLIENTS_DIR / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return []


def get_client_meta(client_id: str) -> dict | None:
    meta_path = CLIENTS_DIR / f"{client_id}.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    return None


# ============ ROUTES ============

@app.route("/")
def index():
    """Landing page: select a client."""
    clients = load_clients()
    recent_runs = sorted(runs.values(), key=lambda r: r["created_at"], reverse=True)[:5]
    return render_template("index.html", clients=clients, recent_runs=recent_runs)


@app.route("/run/start", methods=["POST"])
def start_run():
    """Start a new run for the selected client."""
    client_id = request.form["client_id"]
    client_meta = get_client_meta(client_id)
    if not client_meta:
        return "Client not found", 404

    run_id = str(uuid.uuid4())
    run = {
        "run_id": run_id,
        "client_id": client_id,
        "client_name": client_meta["client_name"],
        "industry": client_meta["industry"],
        "stage": "initialized",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "checkpoints": [],
        "current_checkpoint": None,
        "log": [],
        "output_path": None,
    }
    runs[run_id] = run

    # Start the run in a background thread
    thread = threading.Thread(target=_execute_run, args=(run,), daemon=True)
    thread.start()

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>")
def run_status(run_id: str):
    """View the status of a run."""
    run = runs.get(run_id)
    if not run:
        return "Run not found", 404
    return render_template("run.html", run=run)


@app.route("/run/<run_id>/approve", methods=["POST"])
def approve_checkpoint(run_id: str):
    """Approve the current checkpoint."""
    run = runs.get(run_id)
    if not run or not run["current_checkpoint"]:
        return redirect(url_for("run_status", run_id=run_id))

    feedback = request.form.get("feedback", "")
    run["current_checkpoint"]["status"] = "approved"
    run["current_checkpoint"]["feedback"] = feedback
    run["checkpoints"].append(run["current_checkpoint"])
    run["current_checkpoint"] = None
    run["log"].append(f"Checkpoint approved by operator")

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>/reject", methods=["POST"])
def reject_checkpoint(run_id: str):
    """Reject the current checkpoint."""
    run = runs.get(run_id)
    if not run or not run["current_checkpoint"]:
        return redirect(url_for("run_status", run_id=run_id))

    reason = request.form.get("reason", "No reason provided")
    run["current_checkpoint"]["status"] = "rejected"
    run["current_checkpoint"]["reason"] = reason
    run["checkpoints"].append(run["current_checkpoint"])
    run["current_checkpoint"] = None
    run["stage"] = "rejected"
    run["log"].append(f"Checkpoint rejected: {reason}")

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>/report")
def view_report(run_id: str):
    """View the generated HTML report."""
    run = runs.get(run_id)
    if not run or not run["output_path"]:
        return "Report not available", 404

    report_path = Path(run["output_path"])
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return "Report file not found", 404


@app.route("/api/run/<run_id>/status")
def api_run_status(run_id: str):
    """JSON API for polling run status."""
    run = runs.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


# ============ RUN EXECUTION ============

def _execute_run(run: dict):
    """Execute the analysis workflow with checkpoints."""
    import sys
    sys.path.insert(0, str(BASE_DIR))

    from tools.basic_analysis import run_workforce_analysis
    from tools.chart import generate_headcount_chart
    from tools.report import render_report
    from agent.context import RunContext, Stage
    from agent.model import BedrockModel, ModelConfig
    from storage.local import LocalStorage

    run_id = run["run_id"]
    client_id = run["client_id"]
    csv_path = CLIENTS_DIR / f"{client_id}.csv"

    storage = LocalStorage(base_dir=RUNS_DIR)
    ctx = RunContext(
        client_id=client_id,
        client_name=run["client_name"],
        storage=storage,
        run_id=run_id,
    )

    # Stage 1: Load data
    run["stage"] = "data_loaded"
    run["log"].append("Data loaded for analysis")
    csv_data = csv_path.read_bytes()
    ctx.write_artifact("input", csv_path.name, csv_data)

    # CHECKPOINT 1: Confirm scope
    _wait_for_checkpoint(run, "scope_confirmation",
        "Review the engagement scope",
        f"Workforce analysis for {run['client_name']} ({run['industry']}). "
        f"Dataset contains employee records for headcount analysis, department distribution, "
        f"tenure statistics, and turnover risk assessment.")

    if run["stage"] == "rejected":
        return

    # Stage 2: Run analysis
    run["stage"] = "analyzing"
    run["log"].append("Running workforce analysis...")
    metrics = run_workforce_analysis(csv_path)
    ctx.write_artifact("working", "metrics.json", json.dumps(metrics, indent=2).encode())
    run["metrics"] = {k: v for k, v in metrics.items() if k != "department_breakdown"}
    run["log"].append(f"Analysis complete: {metrics['total_headcount']} employees across {metrics['department_count']} departments")

    # CHECKPOINT 2: Review analysis results
    _wait_for_checkpoint(run, "analysis_review",
        "Review analysis results",
        f"Key findings:\n"
        f"- Total headcount: {metrics['total_headcount']}\n"
        f"- Departments: {metrics['department_count']}\n"
        f"- Largest: {metrics['largest_department']} ({metrics['largest_department_pct']:.1f}%)\n"
        f"- Avg tenure: {metrics['avg_tenure_years']:.1f} years\n"
        f"- High turnover risk: {metrics['turnover_risk_high_pct']:.1f}%\n\n"
        f"Approve to generate visualizations and narrative.")

    if run["stage"] == "rejected":
        return

    # Stage 3: Generate chart
    run["stage"] = "charting"
    run["log"].append("Generating visualizations...")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        chart_path = generate_headcount_chart(metrics, Path(tmp))
        chart_data = chart_path.read_bytes()
    ctx.write_artifact("working", "headcount_by_department.png", chart_data)
    run["log"].append("Chart generated")

    # CHECKPOINT 3: Approve chart / framing
    _wait_for_checkpoint(run, "visualization_review",
        "Review visualization approach",
        "Department headcount bar chart generated. "
        "Approve to proceed with AI narrative generation. "
        "The narrative will interpret the quantitative findings for executive audience.")

    if run["stage"] == "rejected":
        return

    # Stage 4: Generate narrative
    run["stage"] = "generating_narrative"
    run["log"].append("Generating narrative with Claude...")

    try:
        config = ModelConfig(model_id="us.anthropic.claude-sonnet-4-6", profile="intelligence-dev")
        model = BedrockModel(config)
        from agent.agent import _generate_narrative
        narrative = _generate_narrative(ctx, metrics, model)
        ctx.model_id = model.model_id
        run["log"].append("Narrative generated by Claude Sonnet 4.6")
    except Exception as e:
        # Fallback to stub if Bedrock unavailable
        narrative = (
            f"This analysis covers {metrics['total_headcount']} employees across "
            f"{metrics['department_count']} departments for {ctx.client_name}. "
            f"The workforce distribution shows concentration in {metrics['largest_department']} "
            f"which represents {metrics['largest_department_pct']:.1f}% of total headcount. "
            f"Average tenure is {metrics['avg_tenure_years']:.1f} years, with "
            f"{metrics['turnover_risk_high_pct']:.1f}% of employees flagged as high turnover risk."
        )
        ctx.model_id = "stub"
        run["log"].append(f"Narrative generated (stub - Bedrock unavailable: {e})")

    ctx.write_artifact("working", "narrative.txt", narrative.encode())

    # CHECKPOINT 4: Review narrative
    _wait_for_checkpoint(run, "narrative_review",
        "Review narrative draft",
        f"Draft narrative:\n\n{narrative}\n\n"
        f"Approve to render the final report.")

    if run["stage"] == "rejected":
        return

    # Stage 5: Render report
    run["stage"] = "rendering"
    run["log"].append("Rendering final report...")
    report_html = render_report(ctx, metrics, narrative, chart_data)
    output_location = ctx.write_artifact("output", "report.html", report_html.encode("utf-8"))
    run["output_path"] = output_location
    run["log"].append("Report rendered")

    # CHECKPOINT 5: Final review
    _wait_for_checkpoint(run, "final_review",
        "Final report review",
        "The report has been generated and is ready for delivery. "
        "Review the report and approve to mark the engagement as complete.")

    if run["stage"] == "rejected":
        return

    run["stage"] = "completed"
    run["log"].append("Run completed successfully")


def _wait_for_checkpoint(run: dict, name: str, title: str, description: str):
    """Set a checkpoint and wait for operator response."""
    run["stage"] = "waiting_for_approval"
    run["current_checkpoint"] = {
        "name": name,
        "title": title,
        "description": description,
        "status": "pending",
        "requested_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
    run["log"].append(f"Checkpoint: {title}")

    # Poll until resolved
    while run["current_checkpoint"] is not None and run["stage"] != "rejected":
        time.sleep(0.5)


# ============ MAIN ============

if __name__ == "__main__":
    print(f"Intelligence Engine — Consultant UI")
    print(f"  Clients: {len(load_clients())}")
    print(f"  URL: http://localhost:5000")
    print()
    app.run(debug=True, port=5000)

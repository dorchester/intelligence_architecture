"""Consultant-facing web UI for the Intelligence Engine.

Provides a minimal interface for selecting or creating a client, starting a run,
approving checkpoints with feedback, and viewing the final report.
All artifacts are persisted to disk. Bedrock is used for narrative and data generation.
"""

from __future__ import annotations

import csv
import io
import json
import random
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, jsonify

import sys

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

CLIENTS_DIR = BASE_DIR / "sample_data" / "clients"
RUNS_DIR = BASE_DIR / "runs"

# In-memory run state (V0 — production would use DynamoDB)
runs: dict[str, dict] = {}


def load_clients() -> list[dict]:
    manifest_path = CLIENTS_DIR / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return []


def save_client_to_manifest(client_id: str, client_name: str, industry: str):
    """Add a new client to the manifest."""
    manifest_path = CLIENTS_DIR / "manifest.json"
    clients = load_clients()
    clients.append({"client_id": client_id, "client_name": client_name, "industry": industry})
    manifest_path.write_text(json.dumps(clients, indent=2))


def get_client_meta(client_id: str) -> dict | None:
    meta_path = CLIENTS_DIR / f"{client_id}.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    return None


def get_bedrock_model():
    """Get a configured Bedrock model instance."""
    from agent.model import BedrockModel, ModelConfig
    config = ModelConfig(model_id="us.anthropic.claude-sonnet-4-6", profile="intelligence-dev")
    return BedrockModel(config)


# ============ ROUTES ============

@app.route("/")
def index():
    clients = load_clients()
    recent_runs = sorted(runs.values(), key=lambda r: r["created_at"], reverse=True)[:8]
    return render_template("index.html", clients=clients, recent_runs=recent_runs)


@app.route("/run/start", methods=["POST"])
def start_run():
    """Start a new run for an existing client."""
    client_id = request.form["client_id"]
    client_meta = get_client_meta(client_id)
    if not client_meta:
        return "Client not found", 404

    run_id = str(uuid.uuid4())
    run = _create_run_record(run_id, client_id, client_meta["client_name"], client_meta.get("industry", ""))
    runs[run_id] = run

    thread = threading.Thread(target=_execute_run, args=(run,), daemon=True)
    thread.start()

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/start-custom", methods=["POST"])
def start_custom_run():
    """Start a run for a new custom client — generates synthetic data via LLM."""
    client_name = request.form.get("client_name", "").strip()
    industry = request.form.get("industry", "").strip()

    if not client_name:
        return redirect(url_for("index"))

    # Create a client ID from the name
    client_id = "client-" + client_name.lower().replace(" ", "-").replace(".", "")[:30]

    # Check if this client already has data
    csv_path = CLIENTS_DIR / f"{client_id}.csv"
    if not csv_path.exists():
        # Generate synthetic data for this client
        _generate_client_data_with_llm(client_id, client_name, industry)

    run_id = str(uuid.uuid4())
    run = _create_run_record(run_id, client_id, client_name, industry)
    run["custom_client"] = True
    runs[run_id] = run

    thread = threading.Thread(target=_execute_run, args=(run,), daemon=True)
    thread.start()

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>")
def run_status(run_id: str):
    run = runs.get(run_id)
    if not run:
        return "Run not found", 404
    return render_template("run.html", run=run)


@app.route("/run/<run_id>/approve", methods=["POST"])
def approve_checkpoint(run_id: str):
    run = runs.get(run_id)
    if not run or not run["current_checkpoint"]:
        return redirect(url_for("run_status", run_id=run_id))

    feedback = request.form.get("feedback", "").strip()
    cp = run["current_checkpoint"]
    cp["status"] = "approved"
    cp["feedback"] = feedback
    cp["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    # Store feedback in the run log and persist to working artifacts
    if feedback:
        run["log"].append(f"Operator feedback: \"{feedback}\"")
        run["feedback_history"].append({"checkpoint": cp["name"], "feedback": feedback})

    run["checkpoints"].append(cp)
    run["current_checkpoint"] = None
    run["log"].append(f"Checkpoint approved")

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>/reject", methods=["POST"])
def reject_checkpoint(run_id: str):
    run = runs.get(run_id)
    if not run or not run["current_checkpoint"]:
        return redirect(url_for("run_status", run_id=run_id))

    reason = request.form.get("reason", "").strip() or "Rejected by operator"
    cp = run["current_checkpoint"]
    cp["status"] = "rejected"
    cp["reason"] = reason
    cp["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    run["checkpoints"].append(cp)
    run["current_checkpoint"] = None
    run["stage"] = "rejected"
    run["log"].append(f"Rejected: {reason}")

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>/report")
def view_report(run_id: str):
    run = runs.get(run_id)
    if not run or not run["output_path"]:
        return "Report not available yet", 404

    report_path = Path(run["output_path"])
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return "Report file not found on disk", 404


@app.route("/api/run/<run_id>/status")
def api_run_status(run_id: str):
    run = runs.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


# ============ LLM DATA GENERATION ============

def _generate_client_data_with_llm(client_id: str, client_name: str, industry: str):
    """Use Bedrock to generate realistic synthetic workforce data for a custom client."""
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)

    prompt = f"""Generate a realistic synthetic workforce dataset for a company called "{client_name}" in the {industry or 'general'} industry.

Return a JSON object with this exact structure:
{{
  "headcount": <number between 80 and 300>,
  "departments": ["dept1", "dept2", ...],
  "titles_by_dept": {{
    "dept1": ["Title A", "Title B", ...],
    ...
  }}
}}

Requirements:
- 5-8 departments appropriate for a {industry or 'general industry'} company
- 3-5 job titles per department, ranging from junior to senior
- Department names should reflect the actual industry
- Make it realistic for a mid-to-large company in this sector

Return ONLY the JSON object, no other text."""

    try:
        model = get_bedrock_model()
        response = model.invoke(
            messages=[{"role": "user", "content": prompt}],
            system="You are a workforce data generator. Return only valid JSON.",
            temperature=0.7,
        )

        # Parse the JSON from the response
        json_str = response.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0]
        spec = json.loads(json_str)

    except Exception as e:
        # Fallback: generate generic data without LLM
        spec = {
            "headcount": 120,
            "departments": ["Engineering", "Sales", "Operations", "HR", "Finance", "Marketing"],
            "titles_by_dept": {
                "Engineering": ["Software Engineer", "Senior Engineer", "Engineering Manager", "VP Engineering"],
                "Sales": ["Account Executive", "Sales Manager", "VP Sales"],
                "Operations": ["Operations Analyst", "Operations Manager", "VP Operations"],
                "HR": ["HR Generalist", "HR Business Partner", "CHRO"],
                "Finance": ["Financial Analyst", "Controller", "CFO"],
                "Marketing": ["Marketing Coordinator", "Marketing Manager", "VP Marketing"],
            },
        }

    # Generate the CSV from the spec
    _write_client_csv(client_id, client_name, industry, spec)


FIRST_NAMES = [
    "Alex", "Jordan", "Casey", "Taylor", "Morgan", "Riley", "Quinn", "Avery",
    "Dakota", "Jamie", "Skyler", "Rowan", "Charlie", "Emerson", "Finley",
    "Harper", "Sage", "Blair", "Reese", "Peyton", "Drew", "Cameron", "Hayden",
    "Logan", "Parker", "Kai", "River", "Phoenix", "Marley", "Lennon",
    "Remy", "Aspen", "Ellis", "Tatum", "Shiloh", "Wren", "Sutton", "Oakley",
]
LAST_NAMES = [
    "Chen", "Patel", "Kim", "Santos", "Williams", "Johnson", "Rivera",
    "Fischer", "Park", "Nguyen", "Davis", "Campbell", "Thompson", "Burke",
    "Torres", "Yamamoto", "Clarke", "Okafor", "Morrison", "Zhang",
    "Garcia", "Anderson", "Lee", "Martinez", "Brown", "Wilson", "Jackson",
]


def _write_client_csv(client_id: str, client_name: str, industry: str, spec: dict):
    """Write a synthetic workforce CSV and metadata JSON from a spec."""
    headcount = spec.get("headcount", 120)
    departments = spec.get("departments", [])
    titles_by_dept = spec.get("titles_by_dept", {})

    rows = []
    emp_id = 1
    base_per_dept = headcount // len(departments)
    remainder = headcount % len(departments)

    for i, dept in enumerate(departments):
        dept_count = base_per_dept + (1 if i < remainder else 0)
        titles = titles_by_dept.get(dept, ["Employee"])

        for _ in range(dept_count):
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            title = random.choice(titles)
            age = random.randint(22, 62)
            tenure = round(random.uniform(0.3, min(age - 21, 20.0)), 1)

            if tenure < 1.5:
                risk = random.choices(["High", "Medium", "Low"], weights=[50, 35, 15])[0]
            elif tenure < 4:
                risk = random.choices(["High", "Medium", "Low"], weights=[15, 50, 35])[0]
            else:
                risk = random.choices(["High", "Medium", "Low"], weights=[5, 25, 70])[0]

            rows.append({
                "employee_id": f"E{emp_id:04d}",
                "name": f"{first} {last}",
                "department": dept,
                "title": title,
                "age": age,
                "tenure_years": tenure,
                "turnover_risk": risk,
            })
            emp_id += 1

    # Write CSV
    csv_path = CLIENTS_DIR / f"{client_id}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["employee_id", "name", "department", "title", "age", "tenure_years", "turnover_risk"])
        writer.writeheader()
        writer.writerows(rows)

    # Write metadata
    meta = {
        "client_id": client_id,
        "client_name": client_name,
        "industry": industry,
        "headcount": len(rows),
        "departments": departments,
        "generated": True,
    }
    (CLIENTS_DIR / f"{client_id}.json").write_text(json.dumps(meta, indent=2))

    # Update manifest
    save_client_to_manifest(client_id, client_name, industry)


# ============ RUN EXECUTION ============

def _create_run_record(run_id: str, client_id: str, client_name: str, industry: str) -> dict:
    return {
        "run_id": run_id,
        "client_id": client_id,
        "client_name": client_name,
        "industry": industry,
        "stage": "initialized",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "checkpoints": [],
        "current_checkpoint": None,
        "feedback_history": [],
        "log": [],
        "metrics": None,
        "output_path": None,
        "model_used": None,
        "artifacts": [],
    }


def _execute_run(run: dict):
    """Execute the full analysis workflow with 5 checkpoints."""
    from tools.basic_analysis import run_workforce_analysis
    from tools.chart import generate_headcount_chart
    from tools.report import render_report
    from agent.context import RunContext, Stage
    from storage.local import LocalStorage

    run_id = run["run_id"]
    client_id = run["client_id"]
    csv_path = CLIENTS_DIR / f"{client_id}.csv"

    if not csv_path.exists():
        run["stage"] = "error"
        run["log"].append(f"ERROR: No data file found for {client_id}")
        return

    # Set up storage
    storage = LocalStorage(base_dir=RUNS_DIR)
    ctx = RunContext(
        client_id=client_id,
        client_name=run["client_name"],
        storage=storage,
        run_id=run_id,
    )

    # ===== STAGE 1: Load data =====
    run["stage"] = "data_loaded"
    csv_data = csv_path.read_bytes()
    artifact_path = ctx.write_artifact("input", f"{client_id}.csv", csv_data)
    run["artifacts"].append(artifact_path)
    run["log"].append(f"Input data loaded ({len(csv_data)} bytes)")

    # Get basic stats for the checkpoint description
    import pandas as pd
    df = pd.read_csv(csv_path)
    row_count = len(df)
    dept_count = df["department"].nunique()

    # ===== CHECKPOINT 1: Confirm scope =====
    _wait_for_checkpoint(run, "scope_confirmation",
        "Confirm Engagement Scope",
        f"Client: {run['client_name']}\n"
        f"Industry: {run['industry']}\n"
        f"Dataset: {row_count} employee records across {dept_count} departments\n\n"
        f"Analysis will include:\n"
        f"  - Headcount and department distribution\n"
        f"  - Tenure and age statistics\n"
        f"  - Turnover risk assessment\n"
        f"  - AI-generated executive narrative\n\n"
        f"Approve to begin analysis.")
    if run["stage"] == "rejected":
        return

    # ===== STAGE 2: Run analysis =====
    run["stage"] = "analyzing"
    run["log"].append("Running deterministic workforce analysis...")
    metrics = run_workforce_analysis(csv_path)

    # Persist metrics
    metrics_json = json.dumps(metrics, indent=2)
    metrics_path = ctx.write_artifact("working", "metrics.json", metrics_json.encode())
    run["artifacts"].append(metrics_path)
    run["metrics"] = {k: v for k, v in metrics.items() if k != "department_breakdown"}
    run["log"].append(f"Analysis complete: {metrics['total_headcount']} employees, "
                      f"{metrics['department_count']} departments")

    # Include feedback from checkpoint 1 in the log
    _log_prior_feedback(run, "scope_confirmation")

    # ===== CHECKPOINT 2: Review analysis =====
    _wait_for_checkpoint(run, "analysis_review",
        "Review Analysis Results",
        f"Key findings:\n"
        f"  Total headcount: {metrics['total_headcount']}\n"
        f"  Departments: {metrics['department_count']}\n"
        f"  Largest department: {metrics['largest_department']} "
        f"({metrics['largest_department_pct']:.1f}% of workforce)\n"
        f"  Average tenure: {metrics['avg_tenure_years']:.1f} years\n"
        f"  Median tenure: {metrics['median_tenure_years']:.1f} years\n"
        f"  High turnover risk: {metrics['turnover_risk_high_pct']:.1f}% of employees\n"
        f"  Average age: {metrics['avg_age']:.0f}\n\n"
        f"Approve to generate visualizations.")
    if run["stage"] == "rejected":
        return

    # ===== STAGE 3: Generate chart =====
    run["stage"] = "charting"
    run["log"].append("Generating department headcount visualization...")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        chart_path = generate_headcount_chart(metrics, Path(tmp))
        chart_data = chart_path.read_bytes()
    chart_artifact = ctx.write_artifact("working", "headcount_by_department.png", chart_data)
    run["artifacts"].append(chart_artifact)
    run["log"].append(f"Chart saved ({len(chart_data)} bytes)")

    # ===== CHECKPOINT 3: Approve visualization =====
    _wait_for_checkpoint(run, "visualization_review",
        "Review Visualization",
        f"Department headcount bar chart has been generated.\n\n"
        f"Top departments by size:\n" +
        "\n".join(f"  - {dept}: {count}" for dept, count in
                  sorted(metrics["department_breakdown"].items(), key=lambda x: -x[1])[:5]) +
        f"\n\nApprove to proceed with AI narrative generation.\n"
        f"The narrative will interpret these findings for an executive audience.")
    if run["stage"] == "rejected":
        return

    # ===== STAGE 4: Generate narrative via Bedrock =====
    run["stage"] = "generating_narrative"
    run["log"].append("Invoking Claude via Amazon Bedrock for narrative...")

    # Incorporate any operator feedback into the narrative prompt
    feedback_context = _get_all_feedback(run)
    narrative = _generate_narrative_with_feedback(ctx, metrics, run, feedback_context)

    narrative_path = ctx.write_artifact("working", "narrative.txt", narrative.encode())
    run["artifacts"].append(narrative_path)
    run["log"].append(f"Narrative generated ({len(narrative)} chars) via {run['model_used']}")

    # ===== CHECKPOINT 4: Review narrative =====
    _wait_for_checkpoint(run, "narrative_review",
        "Review Narrative Draft",
        f"{narrative}\n\n"
        f"---\n"
        f"Model: {run['model_used']}\n\n"
        f"Approve to render the final report, or provide feedback for revision.")
    if run["stage"] == "rejected":
        return

    # Check if operator gave feedback on narrative — if so, regenerate
    narrative_feedback = _get_feedback_for(run, "narrative_review")
    if narrative_feedback:
        run["log"].append(f"Revising narrative based on feedback: \"{narrative_feedback}\"")
        narrative = _revise_narrative(ctx, metrics, run, narrative, narrative_feedback)
        ctx.write_artifact("working", "narrative_revised.txt", narrative.encode())
        run["log"].append("Narrative revised")

    # ===== STAGE 5: Render report =====
    run["stage"] = "rendering"
    run["log"].append("Rendering final HTML report...")
    report_html = render_report(ctx, metrics, narrative, chart_data)
    output_path = ctx.write_artifact("output", "report.html", report_html.encode("utf-8"))
    run["output_path"] = output_path
    run["artifacts"].append(output_path)
    run["log"].append(f"Report written to: {output_path}")

    # Persist feedback history alongside the report
    feedback_path = ctx.write_artifact("output", "feedback_log.json",
                                       json.dumps(run["feedback_history"], indent=2).encode())
    run["artifacts"].append(feedback_path)

    # ===== CHECKPOINT 5: Final approval =====
    _wait_for_checkpoint(run, "final_review",
        "Final Report Review",
        f"The intelligence report has been generated and is ready for delivery.\n\n"
        f"Artifacts produced:\n"
        f"  - input/{client_id}.csv\n"
        f"  - working/metrics.json\n"
        f"  - working/headcount_by_department.png\n"
        f"  - working/narrative.txt\n"
        f"  - output/report.html\n"
        f"  - output/feedback_log.json\n\n"
        f"Approve to mark this engagement as complete.")
    if run["stage"] == "rejected":
        return

    run["stage"] = "completed"
    run["log"].append("Engagement complete.")


def _generate_narrative_with_feedback(ctx, metrics: dict, run: dict, feedback_context: str) -> str:
    """Generate narrative using Bedrock, incorporating operator feedback."""
    from agent.agent import SYSTEM_PROMPT, NARRATIVE_TEMPLATE

    prompt = NARRATIVE_TEMPLATE.replace("{{client_name}}", ctx.client_name)
    prompt = prompt.replace("{{total_headcount}}", str(metrics["total_headcount"]))
    prompt = prompt.replace("{{department_count}}", str(metrics["department_count"]))
    prompt = prompt.replace("{{largest_department}}", metrics["largest_department"])
    prompt = prompt.replace("{{largest_department_pct}}", f"{metrics['largest_department_pct']:.1f}")
    prompt = prompt.replace("{{avg_tenure_years}}", f"{metrics['avg_tenure_years']:.1f}")
    prompt = prompt.replace("{{median_tenure_years}}", f"{metrics['median_tenure_years']:.1f}")
    prompt = prompt.replace("{{turnover_risk_high_pct}}", f"{metrics['turnover_risk_high_pct']:.1f}")

    if feedback_context:
        prompt += f"\n\nThe consultant reviewing this work has provided the following guidance:\n{feedback_context}\nIncorporate this context where relevant."

    try:
        model = get_bedrock_model()
        narrative = model.invoke(
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        run["model_used"] = model.model_id
        ctx.model_id = model.model_id
        return narrative
    except Exception as e:
        run["model_used"] = "stub (Bedrock unavailable)"
        ctx.model_id = "stub"
        run["log"].append(f"Bedrock error: {e}")
        return (
            f"This analysis covers {metrics['total_headcount']} employees across "
            f"{metrics['department_count']} departments for {ctx.client_name}. "
            f"The workforce shows concentration in {metrics['largest_department']} "
            f"({metrics['largest_department_pct']:.1f}% of headcount). "
            f"Average tenure is {metrics['avg_tenure_years']:.1f} years with "
            f"{metrics['turnover_risk_high_pct']:.1f}% flagged as high turnover risk."
        )


def _revise_narrative(ctx, metrics: dict, run: dict, original: str, feedback: str) -> str:
    """Revise narrative incorporating operator feedback."""
    prompt = (
        f"Here is a workforce analysis narrative that was generated:\n\n"
        f"{original}\n\n"
        f"The reviewing consultant provided this feedback:\n\"{feedback}\"\n\n"
        f"Please revise the narrative to address this feedback. "
        f"Maintain the same professional tone and factual grounding."
    )

    try:
        model = get_bedrock_model()
        return model.invoke(
            messages=[{"role": "user", "content": prompt}],
            system="You are revising a workforce analysis narrative based on consultant feedback. Keep it grounded in the data.",
        )
    except Exception:
        return original  # Keep original if revision fails


def _wait_for_checkpoint(run: dict, name: str, title: str, description: str):
    """Pause execution at a checkpoint until operator responds."""
    run["stage"] = "waiting_for_approval"
    run["current_checkpoint"] = {
        "name": name,
        "title": title,
        "description": description,
        "status": "pending",
        "requested_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
    run["log"].append(f"Awaiting approval: {title}")

    while run["current_checkpoint"] is not None and run["stage"] != "rejected":
        time.sleep(0.3)


def _log_prior_feedback(run: dict, checkpoint_name: str):
    """Log feedback from a specific checkpoint if any was given."""
    for cp in run["checkpoints"]:
        if cp["name"] == checkpoint_name and cp.get("feedback"):
            run["log"].append(f"Incorporating feedback from {checkpoint_name}: \"{cp['feedback']}\"")


def _get_feedback_for(run: dict, checkpoint_name: str) -> str | None:
    """Get feedback text for a specific checkpoint."""
    for cp in run["checkpoints"]:
        if cp["name"] == checkpoint_name and cp.get("feedback"):
            return cp["feedback"]
    return None


def _get_all_feedback(run: dict) -> str:
    """Collect all operator feedback so far into a single string."""
    parts = []
    for cp in run["checkpoints"]:
        if cp.get("feedback"):
            parts.append(f"[{cp['name']}] {cp['feedback']}")
    return "\n".join(parts) if parts else ""


# ============ MAIN ============

if __name__ == "__main__":
    clients = load_clients()
    print(f"Intelligence Engine — Consultant UI")
    print(f"  Clients: {len(clients)}")
    print(f"  Bedrock: us.anthropic.claude-sonnet-4-6 via intelligence-dev profile")
    print(f"  Runs dir: {RUNS_DIR}")
    print(f"  URL: http://localhost:5000")
    print()
    app.run(debug=False, port=5000)

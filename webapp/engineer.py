"""Engineer console.

A separate Flask Blueprint mounted at /engineer. This console is for the
person who builds and operates the engine — it exposes system state,
guardrail configuration, dataset inventory, and per-run traces.

It is deliberately isolated from the consultant console:
  - Different blueprint, different module, different template family
  - No consultant page links into it
  - Can be disabled entirely by not registering the blueprint

In a corporate deployment this blueprint would be served on a separate
route behind different auth (see docs/corporate-deployment-architecture.md).
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from webapp import runtime
from webapp.runtime import guardrails, runs

engineer_bp = Blueprint(
    "engineer",
    __name__,
    url_prefix="/engineer",
    template_folder="templates",
)


@engineer_bp.route("/")
def dashboard():
    """System state: runs, configuration, infrastructure."""
    runs_list = sorted(runs.values(), key=lambda r: r["created_at"], reverse=True)
    active = runtime.active_run_count()

    llm_calls = sum(
        len([e for e in r.get("log", []) if "Bedrock" in e or "generated" in e.lower()])
        for r in runs.values()
    )

    bedrock_status = "connected"
    try:
        runtime.get_bedrock_model()
    except Exception:
        bedrock_status = "error"

    guardrail_events = sum(len(r.get("guardrail_events", [])) for r in runs.values())
    blocked_runs = sum(1 for r in runs.values() if r["stage"] == "blocked")

    dq = runtime.get_dataset_query()
    dataset_count = len(runtime.list_preset_companies()) if dq else 0

    return render_template(
        "engineer.html",
        runs=runs.values(),
        runs_list=runs_list,
        active_runs=active,
        total_llm_calls=llm_calls,
        guardrail_events=guardrail_events,
        blocked_runs=blocked_runs,
        dataset_count=dataset_count,
        runs_dir=str(runtime.RUNS_DIR),
        bedrock_status=bedrock_status,
        model_id=runtime.DEFAULT_MODEL,
        aws_profile=runtime.AWS_PROFILE,
        aws_region=runtime.AWS_REGION,
        bucket=getattr(dq, "bucket", None),
    )


@engineer_bp.route("/run/<run_id>")
def inspect_run(run_id: str):
    """Full technical trace for one run."""
    run = runs.get(run_id)
    if not run:
        return "Run not found", 404
    return render_template("engineer_run.html", run=run)


@engineer_bp.route("/guardrails")
def guardrails_page():
    """Guardrail rules, recent violations, and the YAML editor."""
    guardrails.reload()
    raw = ""
    try:
        raw = guardrails.config_path.read_text(encoding="utf-8")
    except Exception:
        pass

    events = []
    for r in runs.values():
        for e in r.get("guardrail_events", []):
            events.append({
                **e,
                "run_id": r["run_id"],
                "company": r.get("company_name", ""),
            })
    events.reverse()

    return render_template(
        "engineer_guardrails.html",
        config=guardrails.config,
        raw_config=raw,
        config_path=str(guardrails.config_path),
        events=events[:50],
        saved=request.args.get("saved"),
        error=request.args.get("error"),
    )


@engineer_bp.route("/guardrails/save", methods=["POST"])
def guardrails_save():
    """Validate and persist edited guardrail YAML."""
    import yaml

    raw = request.form.get("raw_config", "")
    try:
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Top level must be a mapping")
    except Exception as e:
        return redirect(url_for("engineer.guardrails_page", error=f"Invalid YAML: {e}"))

    try:
        guardrails.config_path.write_text(raw, encoding="utf-8")
        guardrails.reload()
    except Exception as e:
        return redirect(url_for("engineer.guardrails_page", error=str(e)))

    return redirect(url_for("engineer.guardrails_page", saved="1"))


@engineer_bp.route("/datasets")
def datasets():
    """Workforce dataset inventory."""
    companies = runtime.list_preset_companies()
    dq = runtime.get_dataset_query()
    return render_template(
        "engineer_datasets.html",
        companies=companies,
        dataset_error=runtime.dataset_error(),
        bucket=getattr(dq, "bucket", None),
    )


@engineer_bp.route("/datasets/refresh")
def datasets_refresh():
    """Drop the cached S3 client (use after refreshing AWS credentials)."""
    runtime.reset_dataset_client()
    return redirect(url_for("engineer.datasets"))


@engineer_bp.route("/datasets/<company_id>")
def dataset_detail(company_id: str):
    """Inspect one company's dataset, including the exact agent context."""
    dq = runtime.get_dataset_query()
    if not dq:
        return "Dataset service unavailable", 503
    summary = dq.summarize(company_id)
    if not summary:
        return "Dataset not found", 404
    return render_template(
        "engineer_dataset_detail.html",
        summary=summary,
        agent_context=summary.to_agent_context(),
        sample_profiles=dq.get_profiles(company_id, limit=3),
        sample_postings=dq.get_postings(company_id, limit=3),
    )

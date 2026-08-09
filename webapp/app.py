"""Consultant console for the Intelligence Engine.

A pre-engagement intelligence tool for management consultants specializing in
organization, workforce, and change. Generates research-backed briefings about
prospective or current clients.

This module owns only the consultant experience. The engineer console is a
separate blueprint (webapp/engineer.py) with its own routes and templates;
shared runtime state lives in webapp/runtime.py.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from webapp import runtime
from webapp.runtime import (
    RUNS_DIR,
    guardrails,
    get_bedrock_model,
    get_dataset_query,
    list_preset_companies,
    runs,
)

app = Flask(__name__)

# The engineer console is mounted only when enabled. In a corporate
# deployment it would run as a separate service behind different auth.
ENABLE_ENGINEER_CONSOLE = os.environ.get("ENGINEER_CONSOLE", "1") != "0"
if ENABLE_ENGINEER_CONSOLE:
    from webapp.engineer import engineer_bp

    app.register_blueprint(engineer_bp)


# ============ CONSULTANT ROUTES ============

@app.route("/")
def index():
    recent_runs = sorted(runs.values(), key=lambda r: r["created_at"], reverse=True)[:10]
    presets = list_preset_companies()
    return render_template(
        "index.html",
        recent_runs=recent_runs,
        presets=presets,
        dataset_error=runtime.dataset_error(),
        validation_error=request.args.get("error"),
        validation_detail=request.args.get("detail"),
    )


@app.route("/run/start", methods=["POST"])
def start_run():
    company_name = request.form.get("company_name", "").strip()
    engagement_context = request.form.get("engagement_context", "").strip()
    company_id = request.form.get("company_id", "").strip() or None

    if not company_name:
        return redirect(url_for("index"))

    is_preset = bool(company_id)

    # ---- GUARDRAIL: input validation (before any token spend) ----
    guardrails.reload()
    gr = guardrails.validate_input(company_name, engagement_context, is_preset=is_preset)
    if gr.blocked:
        v = gr.blocking_violations[0]
        return redirect(url_for("index", error=v.message, detail=v.detail))

    # ---- GUARDRAIL: concurrency ----
    cr = guardrails.check_concurrent_runs(runtime.active_run_count())
    if cr.blocked:
        v = cr.blocking_violations[0]
        return redirect(url_for("index", error=v.message, detail=v.detail))

    run_id = str(uuid.uuid4())
    run = {
        "run_id": run_id,
        "company_name": company_name,
        "company_id": company_id,
        "is_preset": is_preset,
        "engagement_context": engagement_context,
        "stage": "initialized",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "checkpoints": [],
        "current_checkpoint": None,
        "feedback_history": [],
        "log": [],
        "research": {},
        "dataset_summary": None,
        "dataset_available": False,
        "output_path": None,
        "model_used": None,
        "artifacts": [],
        "guardrail_events": [v.to_dict() for v in gr.violations],
        "revision_counts": {},
    }
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

    if feedback:
        _log(run, f"Consultant direction: \"{feedback}\"")
        run["feedback_history"].append({"checkpoint": cp["name"], "feedback": feedback})

    run["checkpoints"].append(cp)
    run["current_checkpoint"] = None
    _log(run, "Approved - continuing")

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>/revise", methods=["POST"])
def revise_checkpoint(run_id: str):
    """Send the current phase back for revision with feedback."""
    run = runs.get(run_id)
    if not run or not run["current_checkpoint"]:
        return redirect(url_for("run_status", run_id=run_id))

    feedback = request.form.get("feedback", "").strip()
    if not feedback:
        feedback = "Please revise this section."

    cp = run["current_checkpoint"]
    cp["status"] = "revision_requested"
    cp["feedback"] = feedback
    cp["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    _log(run, f"Revision requested: \"{feedback}\"")
    run["feedback_history"].append({"checkpoint": cp["name"], "feedback": feedback, "action": "revise"})
    run["checkpoints"].append(cp)
    run["current_checkpoint"] = None
    # Signal the execution thread to revise (not reject)
    run["_revision_requested"] = {"checkpoint": cp["name"], "feedback": feedback}

    return redirect(url_for("run_status", run_id=run_id))


@app.route("/run/<run_id>/report")
def view_report(run_id: str):
    run = runs.get(run_id)
    if not run or not run["output_path"]:
        return "Report not available yet", 404
    report_path = Path(run["output_path"])
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return "Report file not found", 404


@app.route("/api/run/<run_id>/status")
def api_run_status(run_id: str):
    run = runs.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


# ============ RUN EXECUTION ============

def _execute_run(run: dict):
    """Execute the intelligence workflow:
    1. Research the company's org/workforce via LLM
    2. Identify organizational risks and opportunities
    3. Generate strategic recommendations
    4. Produce a formatted intelligence briefing
    """
    try:
        _execute_run_inner(run)
    except Exception as e:
        run["stage"] = "error"
        _log(run, f"FATAL ERROR: {type(e).__name__}: {str(e).encode('ascii', 'replace').decode()}")


def _execute_run_inner(run: dict):
    from storage.local import LocalStorage
    from agent.context import RunContext

    run_id = run["run_id"]
    company = run["company_name"]
    context = run["engagement_context"]

    storage = LocalStorage(base_dir=RUNS_DIR)
    client_id = "company-" + company.lower().replace(" ", "-").replace(".", "")[:30]
    ctx = RunContext(
        client_id=client_id,
        client_name=company,
        storage=storage,
        run_id=run_id,
    )

    model = get_bedrock_model()
    run["model_used"] = model.model_id

    # ===== PHASE 0a: Entity verification (guardrail) =====
    if guardrails.entity_verification_enabled() and not run.get("is_preset"):
        run["stage"] = "verifying"
        _log(run, f"Verifying '{company}' is a real organization...")
        vcfg = guardrails.entity_verification_config()
        verifier = get_bedrock_model(vcfg.get("model", model.model_id))
        vres, vpayload = guardrails.check_company_exists(company, verifier, is_preset=False)
        _record_guardrails(run, vres)
        run["verification"] = vpayload
        if vres.blocked:
            run["stage"] = "blocked"
            v = vres.blocking_violations[0]
            _log(run, f"BLOCKED: {v.message}")
            run["blocked_reason"] = f"{v.message}. {v.detail}"
            return
        canonical = vpayload.get("canonical_name")
        if canonical and canonical.lower() != company.lower():
            _log(run, f"Resolved to canonical name: {canonical}")
            run["canonical_name"] = canonical
        _log(run, f"Verified (confidence {vpayload.get('confidence', 0):.0%})")

    # ===== PHASE 0b: Load workforce dataset =====
    dataset_context = ""
    dataset_summary = None
    company_id = run.get("company_id")
    if company_id:
        run["stage"] = "loading_dataset"
        _log(run, f"Querying workforce dataset for {company_id}...")
        dq = get_dataset_query()
        if dq and dq.has_dataset(company_id):
            try:
                dataset_summary = dq.summarize(company_id)
            except Exception as e:
                _log(run, f"Dataset query failed: {type(e).__name__}")
            if dataset_summary:
                dataset_context = dataset_summary.to_agent_context()
                run["dataset_available"] = True
                run["dataset_summary"] = dataset_summary.to_dict()
                ctx.write_artifact(
                    "working", "dataset_summary.json",
                    json.dumps(dataset_summary.to_dict(), indent=2).encode(),
                )
                run["artifacts"].append("working/dataset_summary.json")
                _log(
                    run,
                    f"Dataset loaded: {dataset_summary.profiles_analyzed:,} profiles, "
                    f"{dataset_summary.postings_analyzed:,} postings",
                )
        else:
            _log(run, "No dataset available for this company - proceeding with LLM research only")

    # ===== PHASE 1: Company Research =====
    run["stage"] = "researching"
    _log(run, f"Researching {company}...")

    research = _research_company(model, company, context, dataset_context)

    # Guardrail: completeness with retry
    retries = guardrails.research_max_retries()
    for attempt in range(retries):
        rc = guardrails.check_research_completeness(research)
        if not rc.violations:
            break
        _log(run, f"Research incomplete, retrying ({attempt + 1}/{retries})...")
        research = _research_company(model, company, context, dataset_context)
    rc = guardrails.check_research_completeness(research)
    _record_guardrails(run, rc)

    # Guardrail: headcount sanity against dataset
    preset_hc = dataset_summary.headcount if dataset_summary else None
    _record_guardrails(
        run, guardrails.check_company_size(research.get("employee_count"), preset_hc)
    )

    run["research"] = research
    ctx.write_artifact("working", "research.json", json.dumps(research, indent=2).encode())
    run["artifacts"].append("working/research.json")
    _log(run, f"Research complete: {research.get('employee_count', 'Unknown')} employees, "
                      f"{research.get('industry', 'Unknown')} sector")

    # ===== CHECKPOINT 1: Validate research =====
    while True:
        dataset_block = ""
        if dataset_summary:
            dataset_block = (
                f"\nWORKFORCE DATASET (in scope)\n"
                f"  {dataset_summary.profiles_analyzed:,} employee profiles, "
                f"{dataset_summary.postings_analyzed:,} job postings (24 months)\n"
                f"  Avg tenure {dataset_summary.avg_tenure_years:.1f}y | "
                f"flight risk {dataset_summary.flight_risk_pct:.0f}% | "
                f"{dataset_summary.active_postings} open roles\n"
                f"  Hiring trend: {dataset_summary.hiring_velocity_trend}\n"
            )

        revision = _wait_for_checkpoint(run, "research_validation",
            "Validate Company Research",
            f"Company: {research.get('full_name', company)}\n"
            f"Industry: {research.get('industry', 'Unknown')}\n"
            f"Headquarters: {research.get('headquarters', 'Unknown')}\n"
            f"Employees: {research.get('employee_count', 'Unknown')}\n"
            f"Revenue: {research.get('revenue', 'Unknown')}\n"
            f"{dataset_block}\n"
            f"Key business segments:\n{_format_list(research.get('segments', []))}\n\n"
            f"Recent developments:\n{_format_list(research.get('recent_developments', []))}\n\n"
            f"Approve if accurate, or request revision with corrections.")
        if not revision:
            break
        if _revision_limit_hit(run, "research_validation"):
            break
        _log(run, f"Revising research with feedback...")
        run["stage"] = "researching"
        research = _revise_research(model, company, research, revision)
        run["research"] = research
        ctx.write_artifact("working", "research.json", json.dumps(research, indent=2).encode())
        _log(run, "Research revised")

    corrections = _get_feedback_for(run, "research_validation")

    # ===== PHASE 2: Organizational Analysis =====
    run["stage"] = "analyzing_org"
    _log(run, "Analyzing organizational structure and workforce dynamics...")

    org_analysis = _analyze_organization(
        model, company, research, context, corrections, dataset_context
    )
    ctx.write_artifact("working", "org_analysis.json", json.dumps(org_analysis, indent=2).encode())
    run["artifacts"].append("working/org_analysis.json")
    _record_guardrails(
        run,
        guardrails.validate_output(
            org_analysis.get("structure_summary", ""), kind="analysis"
        ),
    )
    _log(run, "Organizational analysis complete")

    # ===== CHECKPOINT 2: Review org analysis =====
    while True:
        revision = _wait_for_checkpoint(run, "org_review",
            "Review Organizational Analysis",
            f"Organizational Structure:\n{org_analysis.get('structure_summary', '')}\n\n"
            f"Workforce Composition:\n{org_analysis.get('workforce_summary', '')}\n\n"
            f"Key Organizational Challenges:\n{_format_list(org_analysis.get('challenges', []))}\n\n"
            f"Talent & Workforce Risks:\n{_format_list(org_analysis.get('workforce_risks', []))}\n\n"
            f"Approve to continue, or request revision with direction.")
        if not revision:
            break
        if _revision_limit_hit(run, "org_review"):
            break
        _log(run, f"Revising org analysis...")
        run["stage"] = "analyzing_org"
        org_analysis = _revise_org_analysis(model, company, research, org_analysis, revision)
        ctx.write_artifact("working", "org_analysis.json", json.dumps(org_analysis, indent=2).encode())
        _log(run, "Org analysis revised")

    focus_direction = _get_feedback_for(run, "org_review")

    # ===== PHASE 3: Strategic Opportunities =====
    run["stage"] = "identifying_opportunities"
    _log(run, "Identifying strategic opportunities for engagement...")

    opportunities = _identify_opportunities(
        model, company, research, org_analysis, context, focus_direction, dataset_context
    )
    ctx.write_artifact("working", "opportunities.json", json.dumps(opportunities, indent=2).encode())
    run["artifacts"].append("working/opportunities.json")
    _log(run, f"Identified {len(opportunities.get('opportunities', []))} potential engagement areas")

    # ===== CHECKPOINT 3: Prioritize opportunities =====
    while True:
        opps_text = ""
        for i, opp in enumerate(opportunities.get("opportunities", []), 1):
            opps_text += f"{i}. {opp.get('title', '')}\n   {opp.get('description', '')}\n   Impact: {opp.get('impact', '')}\n\n"

        revision = _wait_for_checkpoint(run, "opportunity_review",
            "Review & Prioritize Opportunities",
            f"Potential Engagement Areas:\n\n{opps_text}"
            f"Approve to continue, or request revision with guidance on what to change.")
        if not revision:
            break
        if _revision_limit_hit(run, "opportunity_review"):
            break
        _log(run, f"Revising opportunities...")
        run["stage"] = "identifying_opportunities"
        opportunities = _revise_opportunities(model, company, research, org_analysis, opportunities, revision)
        ctx.write_artifact("working", "opportunities.json", json.dumps(opportunities, indent=2).encode())
        _log(run, "Opportunities revised")

    priority_guidance = _get_feedback_for(run, "opportunity_review")

    # ===== PHASE 4: Generate Intelligence Briefing =====
    run["stage"] = "generating_briefing"
    _log(run, "Generating intelligence briefing...")

    briefing = _generate_briefing(model, company, research, org_analysis, opportunities, context,
                                  _get_all_feedback(run), priority_guidance, dataset_context)
    ctx.write_artifact("working", "briefing.md", briefing.encode())
    run["artifacts"].append("working/briefing.md")

    # Guardrail: output validation on the briefing
    bres = guardrails.validate_output(briefing, kind="briefing")
    _record_guardrails(run, bres)
    if bres.blocked:
        run["stage"] = "blocked"
        run["blocked_reason"] = bres.blocking_violations[0].message
        return

    _log(run, f"Briefing drafted ({len(briefing)} chars)")

    # ===== CHECKPOINT 4: Review briefing =====
    while True:
        revision = _wait_for_checkpoint(run, "briefing_review",
            "Review Intelligence Briefing",
            f"{briefing[:3000]}{'...' if len(briefing) > 3000 else ''}\n\n"
            f"---\nApprove to render the final report, or request revision with feedback.")
        if not revision:
            break
        if _revision_limit_hit(run, "briefing_review"):
            break
        _log(run, f"Revising briefing...")
        run["stage"] = "generating_briefing"
        briefing = _revise_briefing(model, briefing, revision)
        ctx.write_artifact("working", "briefing.md", briefing.encode())
        _log(run, "Briefing revised")

    # ===== PHASE 5: Render HTML Report =====
    run["stage"] = "rendering"
    _log(run, "Rendering HTML report...")

    html_report = _render_intelligence_report(model, company, research, briefing)
    output_path = ctx.write_artifact("output", "intelligence_briefing.html", html_report.encode())
    run["output_path"] = output_path
    run["artifacts"].append("output/intelligence_briefing.html")
    _log(run, f"Report saved: {output_path}")

    # ===== CHECKPOINT 5: Final delivery =====
    while True:
        revision = _wait_for_checkpoint(run, "final_review",
            "Approve for Delivery",
            "The intelligence briefing is ready.\n\n"
            "Click 'View Report' below to review the formatted output.\n"
            "Approve to complete, or request revision for final adjustments.")
        if not revision:
            break
        if _revision_limit_hit(run, "final_review"):
            break
        _log(run, f"Revising final report...")
        run["stage"] = "generating_briefing"
        briefing = _revise_briefing(model, briefing, revision)
        html_report = _render_intelligence_report(model, company, research, briefing)
        output_path = ctx.write_artifact("output", "intelligence_briefing.html", html_report.encode())
        run["output_path"] = output_path
        _log(run, "Report revised")

    run["stage"] = "completed"
    _log(run, "Intelligence briefing complete and approved for use.")


# ============ LLM CALLS ============

def _research_company(
    model, company: str, engagement_context: str, dataset_context: str = ""
) -> dict:
    dataset_block = ""
    if dataset_context:
        dataset_block = (
            "\nYou also have access to an internal workforce dataset for this company. "
            "Use it to ground your figures where it conflicts with your recollection:\n\n"
            f"{dataset_context}\n"
        )

    prompt = f"""You are a management consulting researcher. Research the following company based on your knowledge (up to your training cutoff). Provide factual, publicly available information only.

Company: {company}
{f"Engagement context: {engagement_context}" if engagement_context else ""}
{dataset_block}
Return a JSON object with these fields:
{{
  "full_name": "Official company name",
  "industry": "Primary industry/sector",
  "headquarters": "City, State/Country",
  "employee_count": "Approximate number (be specific, e.g. '~155,000' or '40,000-45,000')",
  "revenue": "Most recent annual revenue if public",
  "founded": "Year",
  "public_private": "Public (ticker) or Private",
  "ceo": "Current CEO name",
  "segments": ["Business segment 1", "Business segment 2", ...],
  "recent_developments": [
    "Major recent event/transformation 1",
    "Major recent event/transformation 2",
    "Major recent event/transformation 3"
  ],
  "workforce_context": "2-3 sentences about workforce composition, known challenges, recent layoffs/hiring, union status, etc.",
  "organizational_context": "2-3 sentences about org structure, recent reorgs, leadership changes, operating model"
}}

Be precise about employee counts — use real publicly reported figures. If you're unsure, say "estimated" and give your best figure with a range. Do NOT make up small numbers for large companies.

Return ONLY valid JSON."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are a factual research assistant for management consultants. Return only valid JSON. Be precise about company size and financials based on publicly available information.",
        max_tokens=2000,
    )
    return _parse_json_response(response)


def _analyze_organization(
    model, company: str, research: dict, context: str,
    corrections: str | None, dataset_context: str = "",
) -> dict:
    dataset_block = ""
    if dataset_context:
        dataset_block = (
            "\nEMPIRICAL WORKFORCE DATA (cite these figures directly — they are "
            "measured, not estimated):\n\n" + dataset_context + "\n"
        )

    prompt = f"""You are an organizational effectiveness consultant analyzing {company} for a potential engagement.

Company research:
{json.dumps(research, indent=2)}
{dataset_block}
{f"Engagement context: {context}" if context else ""}
{f"Consultant corrections/additions: {corrections}" if corrections else ""}

Provide a detailed organizational analysis. Return JSON:
{{
  "structure_summary": "3-4 sentences describing the likely organizational structure (divisions, reporting, operating model)",
  "workforce_summary": "3-4 sentences about workforce composition (white collar/blue collar split, geographic distribution, key talent segments)",
  "challenges": [
    "Organizational challenge 1 (specific to this company)",
    "Organizational challenge 2",
    "Organizational challenge 3",
    "Organizational challenge 4"
  ],
  "workforce_risks": [
    "Specific workforce/talent risk 1",
    "Specific workforce/talent risk 2",
    "Specific workforce/talent risk 3"
  ],
  "culture_indicators": "2-3 sentences about known cultural attributes, Glassdoor themes, employer brand",
  "transformation_readiness": "Assessment of organization's readiness/appetite for change based on recent history"
}}

Base everything on publicly available information and reasonable professional inference. Be specific to this company — no generic consulting platitudes.
When empirical workforce data is provided above, cite specific figures from it rather than speaking generally."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are a senior organizational effectiveness consultant. Provide specific, actionable analysis grounded in facts.",
        max_tokens=2500,
    )
    return _parse_json_response(response)


def _identify_opportunities(model, company: str, research: dict, org_analysis: dict,
                           context: str, focus_direction: str | None,
                           dataset_context: str = "") -> dict:
    dataset_block = ""
    if dataset_context:
        dataset_block = (
            "\nEMPIRICAL WORKFORCE DATA (use to size and justify opportunities):\n\n"
            + dataset_context + "\n"
        )

    prompt = f"""You are a business development partner at a management consulting firm specializing in organization, workforce, and change management.

Client: {company}
Research: {json.dumps(research, indent=2)}
Organizational Analysis: {json.dumps(org_analysis, indent=2)}
{dataset_block}
{f"Engagement context: {context}" if context else ""}
{f"Consultant's focus direction: {focus_direction}" if focus_direction else ""}

Identify 4-6 specific consulting engagement opportunities for this client. These should be areas where an organization/workforce/change consultancy could deliver measurable value.

Return JSON:
{{
  "opportunities": [
    {{
      "title": "Short title (e.g. 'Post-Merger Integration Support')",
      "description": "2-3 sentences on what the engagement would involve",
      "impact": "Expected business impact (quantify where possible)",
      "urgency": "High/Medium/Low",
      "service_line": "e.g. Org Design, Workforce Transformation, Change Management, Talent Strategy, Operating Model"
    }}
  ]
}}

Be specific to this company's situation. Reference their actual challenges and recent developments."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are a senior consulting partner identifying real engagement opportunities. Be commercially minded and specific.",
        max_tokens=3000,
    )
    return _parse_json_response(response)


def _generate_briefing(model, company: str, research: dict, org_analysis: dict,
                      opportunities: dict, context: str, all_feedback: str,
                      priority_guidance: str | None, dataset_context: str = "") -> str:
    dataset_block = ""
    if dataset_context:
        dataset_block = (
            "\nEMPIRICAL WORKFORCE DATA — cite these measured figures explicitly in the "
            "briefing. They are the strongest evidence available:\n\n"
            + dataset_context + "\n"
        )

    prompt = f"""Write a pre-engagement intelligence briefing for a management consulting team about to engage with {company}.

Company Research:
{json.dumps(research, indent=2)}

Organizational Analysis:
{json.dumps(org_analysis, indent=2)}

Engagement Opportunities:
{json.dumps(opportunities, indent=2)}
{dataset_block}

{f"Engagement context: {context}" if context else ""}
{f"Consultant's priority guidance: {priority_guidance}" if priority_guidance else ""}
{f"Prior consultant feedback during analysis: {all_feedback}" if all_feedback else ""}

Write a 800-1200 word intelligence briefing structured as:

1. EXECUTIVE SUMMARY (3-4 sentences positioning the client situation)

2. COMPANY OVERVIEW (key facts, recent trajectory, leadership)

3. ORGANIZATIONAL LANDSCAPE
   - Structure and operating model
   - Workforce composition and dynamics
   - Culture and talent signals

4. STRATEGIC CHALLENGES & RISKS
   - The 3-4 most pressing organizational/workforce challenges
   - Why they matter now

5. ENGAGEMENT OPPORTUNITIES
   - Prioritized recommendations for where to focus
   - Expected value/impact for each

6. PREPARATION NOTES
   - Key stakeholders to map
   - Sensitive topics to navigate carefully
   - Competitive intelligence (other consultancies likely involved)

Write for a senior consultant audience. Be direct, specific, and commercially aware. No filler. Every sentence should inform a decision or action."""

    return model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are writing an internal intelligence briefing for senior management consultants. Be incisive, specific, and action-oriented. No consultant jargon without substance.",
        max_tokens=4000,
    )


def _revise_briefing(model, original: str, feedback: str) -> str:
    prompt = f"""Revise this intelligence briefing based on consultant feedback.

Original briefing:
{original}

Feedback:
"{feedback}"

Revise accordingly. Maintain the same structure and professional tone."""

    return model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are revising a consulting intelligence briefing. Maintain quality and specificity.",
        max_tokens=4000,
    )


def _revise_research(model, company: str, research: dict, feedback: str) -> dict:
    """Re-run research incorporating consultant corrections."""
    prompt = f"""You previously researched {company} and produced this:
{json.dumps(research, indent=2)}

The reviewing consultant provided this correction/feedback:
"{feedback}"

Revise the research to address this feedback. Return the SAME JSON structure with corrected information.
Return ONLY valid JSON."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are revising company research based on consultant corrections. Return only valid JSON.",
        max_tokens=2000,
    )
    revised = _parse_json_response(response)
    if "error" not in revised:
        return revised
    return research


def _revise_org_analysis(model, company: str, research: dict, org_analysis: dict, feedback: str) -> dict:
    """Revise organizational analysis based on feedback."""
    prompt = f"""You analyzed {company}'s organization and produced:
{json.dumps(org_analysis, indent=2)}

The consultant provided this direction:
"{feedback}"

Revise the analysis accordingly. Return the SAME JSON structure.
Return ONLY valid JSON."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are revising organizational analysis. Return only valid JSON.",
        max_tokens=2500,
    )
    revised = _parse_json_response(response)
    if "error" not in revised:
        return revised
    return org_analysis


def _revise_opportunities(model, company: str, research: dict, org_analysis: dict, opportunities: dict, feedback: str) -> dict:
    """Revise engagement opportunities based on feedback."""
    prompt = f"""You identified engagement opportunities for {company}:
{json.dumps(opportunities, indent=2)}

The consultant provided this guidance:
"{feedback}"

Revise the opportunities accordingly. Return the SAME JSON structure.
Return ONLY valid JSON."""

    response = model.invoke(
        messages=[{"role": "user", "content": prompt}],
        system="You are revising consulting engagement opportunities. Return only valid JSON.",
        max_tokens=3000,
    )
    revised = _parse_json_response(response)
    if "error" not in revised:
        return revised
    return opportunities


def _render_intelligence_report(model, company: str, research: dict, briefing: str) -> str:
    """Render the briefing as a professional HTML document."""
    import re
    from jinja2 import Template

    # Convert markdown-style briefing to HTML sections
    html_body = briefing.replace("\n\n", "</p><p>").replace("\n", "<br>")
    html_body = f"<p>{html_body}</p>"

    # Bold section headers
    html_body = re.sub(r'<p>(\d+\.\s+[A-Z][A-Z &/\-]+)', r'<h2>\1</h2><p>', html_body)
    html_body = re.sub(r'<br>(\d+\.\s+[A-Z][A-Z &/\-]+)', r'</p><h2>\1</h2><p>', html_body)

    # Format bullet points
    html_body = re.sub(r'<br>\s*[-•]\s*', r'</p><li>', html_body)
    html_body = re.sub(r'<p>\s*[-•]\s*', r'<li>', html_body)

    template = Template("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Intelligence Briefing — {{ company }}</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            max-width: 850px; margin: 3rem auto; padding: 0 2rem;
            color: #1a1a2e; line-height: 1.75; font-size: 15px;
        }
        header { border-bottom: 3px solid #1a1a2e; padding-bottom: 1.5rem; margin-bottom: 2rem; }
        h1 { font-size: 1.8rem; font-weight: 800; margin-bottom: 0.25rem; }
        .meta { color: #6b7280; font-size: 0.85rem; }
        h2 { font-size: 1.1rem; font-weight: 700; margin-top: 2rem; margin-bottom: 0.5rem; color: #1a1a2e;
             border-left: 3px solid #3b82f6; padding-left: 0.75rem; }
        p { margin-bottom: 0.75rem; }
        li { margin-bottom: 0.4rem; margin-left: 1.5rem; }
        .badge { display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 0.2rem 0.6rem;
                 border-radius: 4px; background: #eef2ff; color: #3b82f6; text-transform: uppercase; }
        footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e5e7eb;
                 font-size: 0.75rem; color: #9ca3af; }
    </style>
</head>
<body>
    <header>
        <span class="badge">Pre-Engagement Intelligence</span>
        <h1>{{ company }}</h1>
        <div class="meta">
            {{ industry }} | {{ employee_count }} employees | Generated {{ timestamp }}
        </div>
    </header>
    <main>{{ body }}</main>
    <footer>
        Generated by Intelligence Engine | Model: {{ model }} | Confidential — For internal use only
    </footer>
</body>
</html>""")

    return template.render(
        company=company,
        industry=research.get("industry", ""),
        employee_count=research.get("employee_count", "Unknown"),
        timestamp=time.strftime("%B %d, %Y"),
        body=html_body,
        model="Claude Sonnet 4.6 via Amazon Bedrock",
    )


# ============ HELPERS ============

def _parse_json_response(response: str) -> dict:
    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"error": "Failed to parse response", "raw": response[:500]}


def _format_list(items: list) -> str:
    if not items:
        return "  (none identified)"
    return "\n".join(f"  - {item}" for item in items)


def _safe_str(s: str) -> str:
    """Remove non-ASCII characters that crash Windows console threads."""
    return s.encode("ascii", "replace").decode("ascii")


def _log(run: dict, msg: str):
    """Append a safe log entry."""
    run["log"].append(_safe_str(msg))


def _record_guardrails(run: dict, result) -> None:
    """Record guardrail violations on the run and surface them in the log."""
    if not result or not result.violations:
        return
    for v in result.violations:
        run.setdefault("guardrail_events", []).append(v.to_dict())
        if v.severity in ("block", "warn"):
            _log(run, f"[{v.severity.upper()}] {v.message}"
                      + (f" - {v.detail}" if v.detail else ""))


def _revision_limit_hit(run: dict, checkpoint: str) -> bool:
    """Increment the revision counter and return True if the limit is reached."""
    counts = run.setdefault("revision_counts", {})
    counts[checkpoint] = counts.get(checkpoint, 0) + 1
    res = guardrails.check_revision_limit(counts[checkpoint])
    if res.blocked:
        _record_guardrails(run, res)
        return True
    return False


def _wait_for_checkpoint(run: dict, name: str, title: str, description: str) -> str | None:
    """Wait for consultant response. Returns revision feedback if revision requested, None if approved."""
    run["stage"] = "waiting_for_approval"
    run["current_checkpoint"] = {
        "name": name,
        "title": title,
        "description": description,
        "status": "pending",
        "requested_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
    _log(run, f"Awaiting review: {title}")
    while run["current_checkpoint"] is not None:
        time.sleep(0.3)

    # Check if a revision was requested
    revision = run.pop("_revision_requested", None)
    if revision and revision["checkpoint"] == name:
        return revision["feedback"]
    return None


def _get_feedback_for(run: dict, checkpoint_name: str) -> str | None:
    for cp in run["checkpoints"]:
        if cp["name"] == checkpoint_name and cp.get("feedback"):
            return cp["feedback"]
    return None


def _get_all_feedback(run: dict) -> str:
    parts = []
    for cp in run["checkpoints"]:
        if cp.get("feedback"):
            parts.append(f"[{cp['name']}] {cp['feedback']}")
    return "\n".join(parts) if parts else ""


# ============ MAIN ============

if __name__ == "__main__":
    print("Intelligence Engine — Pre-Engagement Intelligence Tool")
    print(f"  Model: us.anthropic.claude-sonnet-4-6 via Bedrock")
    print(f"  URL: http://localhost:5000")
    print()
    app.run(debug=False, port=5000)

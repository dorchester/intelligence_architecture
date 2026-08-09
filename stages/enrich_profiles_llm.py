"""Stage: the multi-step LLM class.

    python stages/enrich_profiles_llm.py <client_id>

Five patterns that a single-call demo never exercises, and that all bite at
once the first time a real workload runs:

1. **Batched extraction.** Units are sent ~50 at a time, not one per call.
   At one call per profile this stage would be 500 calls against a 50 req/min
   quota - ten minutes of pure waiting before any work happens.
2. **A cached instruction prefix.** The taxonomy is identical across every
   batch, so it is marked cacheable and billed once at write cost and then at
   roughly a tenth. The saving scales with batch count, which is why it is
   invisible in a demo and material in production.
3. **Bounded concurrency with jittered backoff.** Handled in `_bedrock`.
4. **Structured output with a repair round.** A parse failure hands the error
   back rather than retrying an identical prompt.
5. **A second pass, then an advisory reviewer.** Synthesis runs over the
   extractions; the reviewer quotes what it objects to but *cannot fail the
   build*. Deterministic gates decide, models advise, humans read.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import _bedrock
from _aws import config, emit_metric, put_json, read_parquet

BATCH_SIZE = 50

# Identical for every batch, so it is marked cacheable - and at ~2,230 tokens
# it sits BELOW the 4,096-token minimum, so running this stage prints the
# warning from _bedrock rather than a fake saving.
#
# That is deliberate. Caching demonstrably works on this account (4,887 tokens
# caches, 4,082 does not), and the failure mode worth showing is the common
# one: a prompt that looks cached, reports nothing, and bills in full.
# Inflating this taxonomy to clear the bar would demonstrate padding, not
# caching. A production taxonomy reaches 4,096 on its own merits, and when it
# does the warning goes quiet and the saving is real.
EXTRACT_SYSTEM = """You classify workforce profiles for an organisational
capability analysis of a pharmaceutical company. Apply the taxonomy below
exactly. Consistency across batches matters more than nuance in any single
case: the same title must always receive the same classification.

=== DIMENSION 1: capability_area ===
Assign exactly one.

Commercial
  Revenue-generating and market-facing work: field sales, key accounts,
  market access, pricing, payer strategy, brand and product marketing,
  commercial analytics, launch planning, patient services commercial.
  Boundary: analytics sits here when it serves brand or payer decisions, and
  under Technology when it is platform or infrastructure work.

Clinical
  Study design and execution, biostatistics, clinical operations, medical
  affairs, medical science liaison, pharmacovigilance, clinical data
  management, epidemiology, real-world evidence, translational research.
  Boundary: safety reporting is Clinical; safety *submissions* are Regulatory.

Regulatory
  Submissions and health-authority interaction, labelling, CMC regulatory,
  regulatory intelligence, GxP quality assurance, compliance, audit, GCP and
  GMP oversight, inspection readiness.
  Boundary: quality work that governs a production line is Manufacturing;
  quality work that governs a filing is Regulatory.

Manufacturing
  Process development and scale-up, drug substance and drug product, fill and
  finish, biologics and cell culture, validation and tech transfer, supply
  chain, logistics, procurement, site engineering, maintenance.
  Boundary: analytical development that supports a process is Manufacturing;
  bioanalysis supporting a study is Clinical.

Technology
  Software engineering, data engineering and platforms, cloud and
  infrastructure, cybersecurity, enterprise applications, laboratory
  informatics, computational chemistry and biology, machine learning.
  Boundary: a data scientist embedded in commercial analytics is Commercial;
  one building shared pipelines is Technology.

Corporate Functions
  Finance, accounting, tax, treasury, legal, intellectual property, human
  resources, talent acquisition, communications, corporate strategy,
  facilities, administration, executive leadership without a stated function.

=== DIMENSION 2: scarcity ===
Judge the external labour market, not the person's seniority.

common
  Widely available; a competent hire can be sourced in weeks from many
  industries. Most administrative, generalist finance, generalist HR,
  standard IT support, entry commercial roles.

specialised
  Requires sector-specific training or credentials, but a viable pool exists
  within pharma and adjacent industries. Most clinical operations, regulatory
  affairs, quality, process engineering, brand marketing, mid-level data.

scarce
  Small national pool, long lead time, frequently contested between
  employers. Biologics process development, cell and gene therapy, regulatory
  CMC for biologics, pharmacovigilance leadership, health economics,
  ML applied to molecular data, qualified persons, inspection-facing quality
  leadership.

Do not mark something scarce merely because the title is senior. Seniority is
captured separately; scarcity is about replaceability.

=== DIMENSION 3: build_or_buy ===
Whether the organisation more realistically develops this capability
internally or hires it in.

build
  Institutional knowledge dominates: the work depends on knowing this
  company's products, processes, systems, history or relationships. Internal
  progression is normally faster and cheaper than external hiring.

buy
  The capability is portable and the external market can supply it faster
  than internal development. Typical where a step change in scale or a new
  modality is involved, or where the skill is generic across employers.

Where both are defensible, prefer build for roles deep in an existing process
and buy for roles standing up something the company does not yet do.

=== WORKED EXAMPLES ===
"Principal Scientist, Upstream Process Development" with cell culture and
  bioreactor skills -> Manufacturing / scarce / buy. Biologics process
  development is contested and rarely grown quickly in-house.
"Regulatory Affairs Manager" with submissions and labelling skills ->
  Regulatory / specialised / build. Filing history and product knowledge are
  company-specific.
"HR Coordinator" with HRIS and employee relations skills -> Corporate
  Functions / common / build.
"Director, Market Access" with payer strategy and pricing skills ->
  Commercial / specialised / buy. Payer relationships are portable.
"Senior Data Engineer" with pipeline and cloud skills -> Technology /
  specialised / buy.
"Clinical Trial Manager" with GCP and site management skills -> Clinical /
  specialised / build.
"Quality Assurance Specialist, GMP" auditing a production line ->
  Manufacturing / specialised / build.
"Health Economics Lead" with HEOR and modelling skills -> Commercial /
  scarce / buy.

=== FURTHER WORKED EXAMPLES ===
Consistency across batches is the whole point of this list. When a title
resembles one below, classify it the same way.

"Associate Director, Pharmacovigilance" with signal detection and case
  processing skills -> Clinical / scarce / buy. PV leadership is a small,
  contested pool and carries personal regulatory accountability.
"Manufacturing Technician II" with aseptic and batch record skills ->
  Manufacturing / common / build. Site-specific procedure knowledge dominates.
"Patent Counsel" with prosecution and freedom-to-operate skills -> Corporate
  Functions / scarce / buy. Qualified pharma IP attorneys are few and
  portable between employers.
"Sales Representative, Specialty" with territory and account skills ->
  Commercial / common / build.
"Biostatistician" with SAS, mixed models and submissions skills -> Clinical /
  specialised / buy.
"Head of Talent Acquisition" with recruiting and employer brand skills ->
  Corporate Functions / specialised / build.
"Validation Engineer" with CSV, IQ/OQ/PQ and tech transfer skills ->
  Manufacturing / specialised / build.
"Cloud Platform Engineer" with Kubernetes, Terraform and observability skills
  -> Technology / specialised / buy.
"Medical Science Liaison" with KOL engagement and therapeutic area skills ->
  Clinical / specialised / buy. Relationships travel with the person.
"Financial Analyst, FP&A" with forecasting and variance skills -> Corporate
  Functions / common / build.
"Director, CMC Regulatory Affairs (Biologics)" -> Regulatory / scarce / buy.
  The intersection of biologics and CMC filings is the scarcest regulatory
  skill in the sector.
"Warehouse Supervisor" with GDP and inventory skills -> Manufacturing /
  common / build.
"Principal Machine Learning Scientist" with molecular property prediction
  skills -> Technology / scarce / buy.
"Clinical Data Manager" with EDC, CDISC and query management skills ->
  Clinical / specialised / build.
"Brand Manager" with launch planning and omnichannel skills -> Commercial /
  specialised / buy.
"Executive Assistant" -> Corporate Functions / common / build.
"Qualified Person (QP)" with batch release skills -> Regulatory / scarce /
  buy. Statutory role, tiny national pool.

=== WHY THESE DIMENSIONS ===
Read this before classifying; it resolves most borderline cases on its own.

capability_area exists to answer "which part of the operating model does this
person belong to". It is about the work, not the reporting line. A finance
business partner embedded in a manufacturing site is Corporate Functions,
because the work is finance. A process engineer reporting into a corporate
technical function is Manufacturing, because the work is process.

scarcity exists to answer "how long would replacing this take, and how
contested is the hire". It is a statement about the external market, never
about performance, never about seniority, and never about how important the
role feels internally. A chief of staff is important and not scarce. A single
qualified person is unremarkable in the hierarchy and extremely scarce.

build_or_buy exists to answer "where should the organisation put its effort
if it needs more of this". Build means internal development is the faster and
cheaper route because company-specific knowledge dominates. Buy means the
market can supply it faster than internal development, usually because the
skill transfers cleanly between employers.

The three interact. Scarce and buy together signal an acquisition problem
that money alone may not solve, and those roles carry the highest
single-point-of-failure risk. Scarce and build together signal a succession
problem: the capability exists internally and cannot be replaced quickly from
outside, so losing the holder is expensive. Common and buy together is the
lowest-risk combination and rarely needs comment in an analysis.

Apply the taxonomy mechanically. Do not editorialise, do not hedge, and do
not add fields. A downstream stage counts these classifications and any
deviation from the six areas or three scarcity levels corrupts the totals.

=== EDGE HANDLING ===
If a title is ambiguous or generic ("Analyst", "Manager", "Specialist" with
no qualifier), use the department field to decide capability_area, default
scarcity to common, and default build_or_buy to build.
If skills contradict the title, trust the skills.
If a profile appears to sit across two areas, choose the one its skills
support most, never a blend.
Never invent a category outside the six listed. Never leave a field blank.

=== OUTPUT ===
Reply with JSON only, a list of objects, one per profile, in the order given:
  {"i": <index>, "capability_area": "...", "scarcity": "...", "build_or_buy": "..."}
Every profile you were given must appear exactly once. No prose, no code
fences, no commentary."""

SYNTHESIS_SYSTEM = """You are summarising an organisational capability
profile from already-classified counts. Be specific and quantitative. Never
introduce a number that is not in the input. Reply with JSON only:
  {"headline": "...", "capability_risks": ["...", "..."],
   "build_recommendations": ["..."], "buy_recommendations": ["..."]}"""

REVIEW_SYSTEM = """You are an advisory reviewer. You cannot block anything;
your job is to flag what a human should look at.

Quote the exact text you object to. Reply with JSON only:
  {"concerns": [{"quote": "...", "why": "..."}], "overall": "..."}"""


def batch_extract(batch: list[dict], offset: int) -> list[dict]:
    """One model call for up to BATCH_SIZE profiles."""
    lines = [
        f'{i}: title="{p.get("title","")}" dept="{p.get("department","")}" '
        f'level="{p.get("seniority_level","")}" skills="{str(p.get("skills",""))[:160]}"'
        for i, p in enumerate(batch)
    ]
    user = "Classify these profiles:\n" + "\n".join(lines)
    result = _bedrock.invoke_json(EXTRACT_SYSTEM, user, max_tokens=4000)
    if isinstance(result, dict):
        result = result.get("profiles", [])
    for row in result:
        row["i"] = int(row.get("i", 0)) + offset
    return result


def main(client_id: str) -> None:
    started = time.time()
    cfg = config()
    df = read_parquet(cfg["lakehouse_bucket"],
                      f"silver/profiles/client_id={client_id}/part-0000.parquet")
    print(f"read {len(df):,} conformed rows")

    feedback = os.environ.get("REVISION_FEEDBACK", "").strip()
    if feedback:
        print(f"revision requested: {feedback}")

    records = df.to_dict("records")
    batches = [(records[i:i + BATCH_SIZE], i)
               for i in range(0, len(records), BATCH_SIZE)]
    print(f"{len(records):,} profiles -> {len(batches)} calls "
          f"(one per {BATCH_SIZE}, not one per profile)")

    # Concurrency is bounded inside _bedrock by a semaphore, so this pool can
    # be wider than the quota allows without stampeding it.
    extracted: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for rows in pool.map(lambda b: batch_extract(*b), batches):
            extracted.extend(rows)
    print(f"extracted {len(extracted):,} classifications")

    # Counts are computed in Python, not asked of the model. A model that is
    # allowed to count will eventually miscount.
    def tally(field: str) -> dict:
        out: dict[str, int] = {}
        for row in extracted:
            out[row.get(field, "unknown")] = out.get(row.get(field, "unknown"), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    counts = {f: tally(f) for f in ("capability_area", "scarcity", "build_or_buy")}

    synthesis_input = json.dumps({"client": client_id, "counts": counts}, indent=1)
    if feedback:
        synthesis_input += f"\n\nReviewer direction to incorporate: {feedback}"
    synthesis = _bedrock.invoke_json(SYNTHESIS_SYSTEM, synthesis_input, max_tokens=1200)

    review = _bedrock.invoke_json(
        REVIEW_SYSTEM,
        "Review this summary against the counts it claims to describe.\n\n"
        f"COUNTS:\n{json.dumps(counts, indent=1)}\n\nSUMMARY:\n{json.dumps(synthesis, indent=1)}",
        max_tokens=1200,
    )

    concerns = review.get("concerns", []) if isinstance(review, dict) else []
    for c in concerns:
        print(f"ADVISORY | {c.get('why','')} <- \"{str(c.get('quote',''))[:90]}\"")
    print(f"reviewer raised {len(concerns)} concern(s) - advisory only, "
          f"the build is not gated on them")

    payload = {
        "client_id": client_id,
        "profiles_classified": len(extracted),
        "counts": counts,
        "synthesis": synthesis,
        "advisory_review": review,
        "revision_feedback": feedback or None,
        "model_usage": _bedrock.usage,
    }
    key = f"analysis/{client_id}/capability_enrichment.json"
    put_json(cfg["artifacts_bucket"], key, payload)

    elapsed = time.time() - started
    emit_metric("enrich_profiles_llm", "DurationSeconds", elapsed, "Seconds")
    emit_metric("enrich_profiles_llm", "ModelCalls", float(_bedrock.usage["calls"]), "Count")

    print(f"USAGE | {_bedrock.summary()}")
    print(f"ENRICH OK in {elapsed:.1f}s -> s3://{cfg['artifacts_bucket']}/{key}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: enrich_profiles_llm.py <client_id>")
    main(sys.argv[1])

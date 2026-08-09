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

# Identical for every batch, so it is worth caching. Everything that varies
# goes in the user turn.
EXTRACT_SYSTEM = """You classify workforce profiles for an organisational
analysis. For each profile you are given, decide:

  capability_area : one of [Commercial, Clinical, Regulatory, Manufacturing,
                            Technology, Corporate Functions]
  scarcity        : one of [common, specialised, scarce]
  build_or_buy    : one of [build, buy] - whether this capability is more
                    realistically developed internally or hired in

Reply with JSON only, as a list of objects, one per profile, in the order
given, each of the form:
  {"i": <index>, "capability_area": "...", "scarcity": "...", "build_or_buy": "..."}
No prose. No code fences."""

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

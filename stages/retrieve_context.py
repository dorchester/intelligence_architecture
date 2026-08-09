"""Stage: the two retrieval demos over the contextualized tier.

    python stages/retrieve_context.py <client_id> "<question>"

**Hybrid search** - metadata filter, then ANN, then assemble. The order is the
point. Filtering first shrinks the candidate set to rows the caller is
entitled to and interested in; ranking first would score the whole corpus and
then discard most of it, which costs more and leaks the shape of what was
excluded.

**Graph traversal** - answers a question by walking typed edges rather than by
flat lookup. The demo question, "which skills bridge two departments", is one
traversal (Department <- Person -> Skill, intersected) and an awkward
self-join with no natural key in a flat table. That difference is the whole
argument for the tier.

Neither demo needs a managed service. Both would move to one unchanged in
meaning.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from _aws import config, emit_metric, put_json, read_parquet, session
from _governance import flush_steward_log, gate_engagement_permissibility

TOP_K = 5


def embed_query(text: str, model_id: str) -> np.ndarray:
    br = session().client("bedrock-runtime")
    r = br.invoke_model(modelId=model_id, body=json.dumps(
        {"inputText": text, "dimensions": 256, "normalize": True}))
    return np.array(json.loads(r["body"].read())["embedding"], dtype=float)


def hybrid_search(vectors: pd.DataFrame, query: str, model_id: str,
                  department: str | None = None,
                  seniority: str | None = None) -> pd.DataFrame:
    candidates = vectors
    applied = []
    if department:
        candidates = candidates[candidates["department"] == department]
        applied.append(f"department={department}")
    if seniority:
        candidates = candidates[candidates["seniority_level"] == seniority]
        applied.append(f"seniority={seniority}")
    print(f"filter {applied or ['none']}: {len(vectors)} -> {len(candidates)} candidates")

    if candidates.empty:
        return candidates

    q = embed_query(query, model_id)
    mat = np.array([json.loads(v) for v in candidates["embedding"]], dtype=float)
    # Vectors are stored normalised, so a dot product is cosine similarity.
    scores = mat @ q
    out = candidates.assign(score=scores).sort_values("score", ascending=False)
    return out.head(TOP_K)[["profile_id", "department", "seniority_level",
                            "document", "score"]]


def bridging_skills(edges: pd.DataFrame, dept_a: str, dept_b: str) -> list[tuple[str, int]]:
    """Skills held by people in both departments - one traversal each way."""
    works = edges[edges["edge_type"] == "WORKS_IN"]
    has = edges[edges["edge_type"] == "HAS_SKILL"]

    people_a = set(works[works["dst"] == f"dept:{dept_a}"]["src"])
    people_b = set(works[works["dst"] == f"dept:{dept_b}"]["src"])

    skills_a = has[has["src"].isin(people_a)]
    skills_b = has[has["src"].isin(people_b)]
    shared = set(skills_a["dst"]) & set(skills_b["dst"])

    counts = []
    for s in shared:
        n = int((skills_a["dst"] == s).sum() + (skills_b["dst"] == s).sum())
        counts.append((s.split(":", 1)[1], n))
    return sorted(counts, key=lambda kv: -kv[1])[:8]


def main(client_id: str, question: str) -> None:
    cfg = config()
    lake, ctx = cfg["lakehouse_bucket"], "contextualized"

    gate_engagement_permissibility(client_id, purpose="retrieval")

    vectors = read_parquet(lake, f"{ctx}/profile_vectors/client_id={client_id}/part-0000.parquet")
    edges = read_parquet(lake, f"{ctx}/graph_edges/client_id={client_id}/part-0000.parquet")
    print(f"read {len(vectors)} vectors and {len(edges):,} edges "
          "through contextualized-read")

    print(f"\n--- hybrid search: {question!r} ---")
    hits = hybrid_search(vectors, question, cfg["embedding_model_id"],
                         department=None, seniority="Senior")
    for _, h in hits.iterrows():
        print(f"  {h['score']:.3f}  {h['department'][:26]:28} {h['document'][:70]}")

    depts = (edges[edges["edge_type"] == "WORKS_IN"]["dst"]
             .value_counts().head(2).index.tolist())
    a, b = (d.split(":", 1)[1] for d in depts)
    print(f"\n--- graph traversal: skills bridging {a!r} and {b!r} ---")
    bridges = bridging_skills(edges, a, b)
    for skill, n in bridges:
        print(f"  {n:>4}  {skill}")

    put_json(cfg["artifacts_bucket"], f"analysis/{client_id}/retrieval_demo.json", {
        "client_id": client_id,
        "question": question,
        "hybrid_search": {
            "filter": {"seniority_level": "Senior"},
            "candidates_after_filter": int(len(hits)),
            "top_k": hits.drop(columns=["document"]).to_dict("records") if not hits.empty else [],
        },
        "graph_traversal": {
            "departments": [a, b],
            "bridging_skills": [{"skill": s, "weight": n} for s, n in bridges],
        },
    })

    emit_metric("retrieve_context", "HybridHits", float(len(hits)), "Count")
    flush_steward_log()
    print(f"\nRETRIEVAL OK | {len(hits)} ranked hits | "
          f"{len(bridges)} bridging skills")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: retrieve_context.py <client_id> ["question"]')
    q = sys.argv[2] if len(sys.argv) > 2 else "regulatory submissions experience"
    main(sys.argv[1], q)

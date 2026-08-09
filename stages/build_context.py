"""Stage: build the L4 contextualized tier - embeddings and a typed graph.

    python stages/build_context.py <client_id>

Two retrieval structures, both as plain catalogue tables. No managed vector
service and no graph database: the argument is that the *pattern* is what
needs proving, and a table plus one traversal proves it. Swapping in OpenSearch
or Neptune later changes where the rows live, not what the retrieval means.

**Vector shell.** Each profile becomes one short document, embedded through
Bedrock Titan, stored alongside the metadata a filter needs. Metadata lives
next to the vector deliberately - hybrid search filters *before* it ranks, and
a vector store that cannot filter forces the whole corpus through the ANN step
and then throws most of it away.

**Graph shell.** Typed nodes and edges over the same population:

    (Person)-[:WORKS_IN]->(Department)
    (Person)-[:HOLDS]->(Title)
    (Person)-[:HAS_SKILL]->(Skill)
    (Title)-[:AT_LEVEL]->(SeniorityBand)

Edges are what make a traversal answer questions a flat lookup cannot -
"which skills bridge two departments" is one hop in a graph and a self-join
with no natural key in a table.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from _aws import config, emit_metric, put_parquet, read_parquet, session
from _governance import flush_steward_log, gate_engagement_permissibility

# Keep the demo bounded: embedding every profile is not the point being made.
EMBED_LIMIT = 120
EMBED_DIMS = 256


def embed(texts: list[str], model_id: str) -> list[list[float]]:
    """Embed one at a time - Titan's invoke API takes a single input."""
    br = session().client("bedrock-runtime")
    out = []
    for i, t in enumerate(texts):
        body = json.dumps({"inputText": t, "dimensions": EMBED_DIMS,
                           "normalize": True})
        r = br.invoke_model(modelId=model_id, body=body)
        out.append(json.loads(r["body"].read())["embedding"])
        if (i + 1) % 40 == 0:
            print(f"  embedded {i + 1}/{len(texts)}")
    return out


def document(row: pd.Series) -> str:
    """One profile as retrieval text. Structural attributes only."""
    return (f"{row.get('title','')} in {row.get('department','')}. "
            f"Seniority {row.get('seniority_level','')}. "
            f"Skills: {str(row.get('skills',''))[:220]}")


def build_vectors(df: pd.DataFrame, model_id: str) -> pd.DataFrame:
    sample = df.head(EMBED_LIMIT).copy()
    docs = [document(r) for _, r in sample.iterrows()]
    print(f"embedding {len(docs)} documents at {EMBED_DIMS} dimensions")
    vectors = embed(docs, model_id)

    return pd.DataFrame({
        "profile_id": sample["profile_id"].astype(str).values,
        "document": docs,
        # Metadata sits beside the vector so a filter can run before the ANN
        # step rather than after it.
        "department": sample["department"].astype(str).values,
        "seniority_level": sample["seniority_level"].astype(str).values,
        "embedding": [json.dumps(v) for v in vectors],
        "dims": EMBED_DIMS,
    })


def build_graph(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes, edges = [], []
    seen = set()

    def node(nid: str, ntype: str, label: str):
        if nid not in seen:
            seen.add(nid)
            nodes.append({"node_id": nid, "node_type": ntype, "label": label})

    for _, r in df.iterrows():
        pid = f"person:{r['profile_id']}"
        dept, title = str(r.get("department", "")), str(r.get("title", ""))
        band = str(r.get("seniority_level", ""))
        node(pid, "Person", str(r["profile_id"]))
        if dept:
            node(f"dept:{dept}", "Department", dept)
            edges.append({"src": pid, "edge_type": "WORKS_IN", "dst": f"dept:{dept}"})
        if title:
            node(f"title:{title}", "Title", title)
            edges.append({"src": pid, "edge_type": "HOLDS", "dst": f"title:{title}"})
            if band:
                node(f"band:{band}", "SeniorityBand", band)
                edges.append({"src": f"title:{title}", "edge_type": "AT_LEVEL",
                              "dst": f"band:{band}"})
        for skill in str(r.get("skills", "")).split("|"):
            skill = skill.strip()
            if skill:
                node(f"skill:{skill}", "Skill", skill)
                edges.append({"src": pid, "edge_type": "HAS_SKILL",
                              "dst": f"skill:{skill}"})

    e = pd.DataFrame(edges).drop_duplicates()
    return pd.DataFrame(nodes), e


def register(database: str, bucket: str, client_id: str, name: str,
             df: pd.DataFrame, source: str, note: str) -> None:
    glue = session().client("glue")

    def gtype(dtype) -> str:
        return {"i": "bigint", "f": "double", "b": "boolean"}.get(dtype.kind, "string")

    location = f"s3://{bucket}/contextualized/{name}/"
    storage = {
        "Columns": [{"Name": c, "Type": gtype(df[c].dtype)} for c in df.columns],
        "Location": location,
        "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
        "SerdeInfo": {"SerializationLibrary":
                      "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"},
    }
    table_input = {
        "Name": name,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "classification": "parquet", "EXTERNAL": "TRUE",
            "ie.tier": "contextualized",
            "ie.owner": "workforce-analytics@intelligence-engine.invalid",
            "ie.lineage.source_table": source,
            "ie.lineage.built_by": "stages/build_context.py",
            "ie.note": note,
        },
        "PartitionKeys": [{"Name": "client_id", "Type": "string"}],
        "StorageDescriptor": storage,
    }
    try:
        glue.create_table(DatabaseName=database, TableInput=table_input)
        print(f"created {database}.{name}")
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(DatabaseName=database, TableInput=table_input)
        print(f"updated {database}.{name}")

    part = {"Values": [client_id],
            "StorageDescriptor": {**storage, "Location": f"{location}client_id={client_id}/"}}
    try:
        glue.create_partition(DatabaseName=database, TableName=name, PartitionInput=part)
    except glue.exceptions.AlreadyExistsException:
        glue.update_partition(DatabaseName=database, TableName=name,
                              PartitionValueList=[client_id], PartitionInput=part)


def main(client_id: str) -> None:
    cfg = config()
    lake, ctx_db = cfg["lakehouse_bucket"], cfg["contextualized_db"]

    gate_engagement_permissibility(client_id, purpose="retrieval structure build")

    src = read_parquet(lake, f"foundational/profiles/client_id={client_id}/part-0000.parquet")
    print(f"read {len(src):,} foundational rows")

    vectors = build_vectors(src, cfg["embedding_model_id"])
    put_parquet(vectors, lake, f"contextualized/profile_vectors/client_id={client_id}/part-0000.parquet")
    register(ctx_db, lake, client_id, "profile_vectors", vectors,
             f"{cfg['foundational_db']}.profiles",
             "embeddings with filterable metadata for hybrid search")

    nodes, edges = build_graph(src)
    put_parquet(nodes, lake, f"contextualized/graph_nodes/client_id={client_id}/part-0000.parquet")
    put_parquet(edges, lake, f"contextualized/graph_edges/client_id={client_id}/part-0000.parquet")
    register(ctx_db, lake, client_id, "graph_nodes", nodes,
             f"{cfg['foundational_db']}.profiles", "typed nodes")
    register(ctx_db, lake, client_id, "graph_edges", edges,
             f"{cfg['foundational_db']}.profiles",
             "typed edges: WORKS_IN, HOLDS, HAS_SKILL, AT_LEVEL")

    emit_metric("build_context", "VectorsBuilt", float(len(vectors)), "Count")
    emit_metric("build_context", "EdgesBuilt", float(len(edges)), "Count")
    flush_steward_log()
    print(f"CONTEXT OK | {len(vectors)} vectors | "
          f"{len(nodes):,} nodes, {len(edges):,} edges")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: build_context.py <client_id>")
    main(sys.argv[1])

"""Stage: conform L1 source records into the L2 foundational tier.

    python stages/conform_to_foundational.py <client_id>

This is the stage that makes the `foundational-read` grant meaningful. Before it
runs, the lakehouse is empty and every downstream grant points at nothing.

The important step is not the file format - it is that **direct identifiers
are dropped here**. L1 may hold raw records; foundational is what analytical
stages are allowed to read, so the removal happens on the way in rather than
being left to every downstream consumer to remember.

Be precise about what this does and does not achieve. Removing names and
banding age makes these records **pseudonymous, not anonymous**. A row still
carries department, location, title, seniority, tenure and a stable
`profile_id`, and that combination is re-identifying in a small population.
Under GDPR and equivalent regimes pseudonymous data is still personal data:
it narrows exposure and shortens a review, it does not remove the obligation.
Treat `foundational/*` as governed personal data, not as a public-safe extract.

`turnover_risk_score` deserves particular attention: it is an inferred
judgement about an individual's likelihood of leaving. Derived attributes of
that kind are often more sensitive than the identifiers that were removed,
and they are the reason a real deployment needs a documented lawful basis
before this data is put in front of a model.

Writes:
  s3://<lakehouse>/foundational/profiles/client_id=<id>/part-0000.parquet
and registers the partition in the Glue catalog so Athena can query it.
"""
from __future__ import annotations

import sys
import pandas as pd

from _aws import config, emit_metric, put_parquet, read_jsonl, session
from _governance import (flush_steward_log, gate_anonymization,
                         gate_engagement_permissibility)

# Dropped, never written to foundational. Everything analytical is structural.
DIRECT_IDENTIFIERS = ["first_name", "last_name", "full_name", "headline"]

TABLE = "profiles"


def conform(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)

    dropped = [c for c in DIRECT_IDENTIFIERS if c in df.columns]
    df = df.drop(columns=dropped)
    print(f"dropped direct identifiers: {dropped}")

    # Education is a nested object; keep only the non-identifying part.
    if "education" in df.columns:
        df["education_level"] = df["education"].apply(
            lambda e: (e or {}).get("degree", "").split(" in ")[0] if isinstance(e, dict) else None
        )
        df = df.drop(columns=["education"])

    # Skills become a count plus a delimited string - lists do not survive a
    # Glue string column cleanly, and the count is what analysis actually uses.
    if "skills" in df.columns:
        df["skill_count"] = df["skills"].apply(lambda s: len(s) if isinstance(s, list) else 0)
        df["skills"] = df["skills"].apply(
            lambda s: "|".join(s) if isinstance(s, list) else ""
        )

    # Age becomes a band. A precise age plus department plus location is
    # re-identifying in a small population; a band is not.
    if "age" in df.columns:
        df["age_band"] = pd.cut(
            df["age"], bins=[0, 29, 39, 49, 59, 200],
            labels=["<30", "30-39", "40-49", "50-59", "60+"],
        ).astype(str)
        df = df.drop(columns=["age"])

    for col in ("tenure_years",):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def register_glue_partition(database: str, bucket: str, client_id: str, df: pd.DataFrame) -> None:
    """Create the table if absent, then add this client's partition."""
    glue = session().client("glue")

    def gtype(dtype) -> str:
        k = dtype.kind
        return {"i": "bigint", "f": "double", "b": "boolean"}.get(k, "string")

    columns = [{"Name": c, "Type": gtype(df[c].dtype)} for c in df.columns]
    location = f"s3://{bucket}/foundational/profiles/"
    storage = {
        "Columns": columns,
        "Location": location,
        "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
        "SerdeInfo": {
            "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
        },
    }
    table_input = {
        "Name": TABLE,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {"classification": "parquet", "EXTERNAL": "TRUE"},
        "PartitionKeys": [{"Name": "client_id", "Type": "string"}],
        "StorageDescriptor": storage,
    }

    try:
        glue.create_table(DatabaseName=database, TableInput=table_input)
        print(f"created glue table {database}.{TABLE}")
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(DatabaseName=database, TableInput=table_input)
        print(f"updated glue table {database}.{TABLE}")

    part_location = f"{location}client_id={client_id}/"
    part_input = {
        "Values": [client_id],
        "StorageDescriptor": {**storage, "Location": part_location},
    }
    try:
        glue.create_partition(DatabaseName=database, TableName=TABLE,
                              PartitionInput=part_input)
        print(f"registered partition client_id={client_id}")
    except glue.exceptions.AlreadyExistsException:
        glue.update_partition(DatabaseName=database, TableName=TABLE,
                              PartitionValueList=[client_id], PartitionInput=part_input)
        print(f"updated partition client_id={client_id}")


def main(client_id: str) -> None:
    cfg = config()
    source_bucket = cfg["source_bucket"]
    lakehouse = cfg["lakehouse_bucket"]
    database = cfg["foundational_db"]

    # Gate (a): permissibility, before anything is read.
    gate_engagement_permissibility(client_id, purpose="organisational analysis")

    records = read_jsonl(source_bucket, f"datasets/{client_id}/profiles.jsonl")
    print(f"read {len(records):,} landing records for {client_id}")

    df = conform(records)

    # Gate (b): anonymisation, before anything is persisted. This raises
    # rather than warns if a direct identifier survived conformance.
    gate_anonymization(df, tier="foundational")

    key = f"foundational/profiles/client_id={client_id}/part-0000.parquet"
    written = put_parquet(df, lakehouse, key)
    print(f"wrote s3://{lakehouse}/{key} ({written:,} bytes, {len(df.columns)} columns)")

    register_glue_partition(database, lakehouse, client_id, df)

    emit_metric("conform_to_foundational", "RowsConformed", float(len(df)), "Count")
    flush_steward_log()
    print(f"CONFORM OK | {len(df):,} rows | columns: {', '.join(sorted(df.columns))}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: conform_to_foundational.py <client_id>")
    main(sys.argv[1])

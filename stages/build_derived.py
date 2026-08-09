"""Stage: build one L3 derived product from the foundational tier.

    python stages/build_derived.py <client_id>

The middle link in the connective thread: one synthetic drop -> conformance
writes foundational -> **this product, registered with owner and lineage** ->
one governed, logged read.

What makes it a *product* rather than an output file is the catalogue entry.
The Glue table carries an owner, a lineage pointer to the table it was built
from, the stage that built it, and the aggregation level. A consumer can
therefore answer "where did this come from and who owns it" without reading
the code that produced it - which is the whole claim of a governed tier.

Aggregation matters here. The product is **aggregate-only**: one row per
department and seniority band, with small cells suppressed. IAM cannot
inspect aggregation level, so that rule is enforced in code, at the point of
writing, and recorded in the table's own metadata.
"""
from __future__ import annotations

import datetime
import sys

import pandas as pd

from _aws import config, emit_metric, put_parquet, read_parquet, session
from _governance import (flush_steward_log, gate_anonymization,
                         gate_engagement_permissibility)

TABLE = "workforce_composition"
SOURCE_TABLE = "profiles"
OWNER = "workforce-analytics@intelligence-engine.invalid"

# Cells smaller than this are suppressed rather than published. A count of one
# in a department-by-band cell is a person, not a statistic.
MIN_CELL = 5


def build(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (df.groupby(["department", "seniority_level"], dropna=False)
                 .agg(headcount=("profile_id", "count"),
                      mean_tenure_years=("tenure_years", "mean"),
                      mean_skill_count=("skill_count", "mean"))
                 .reset_index())

    suppressed = grouped[grouped["headcount"] < MIN_CELL]
    kept = grouped[grouped["headcount"] >= MIN_CELL].copy()
    print(f"aggregated to {len(grouped)} cells; suppressed {len(suppressed)} "
          f"below the minimum cell size of {MIN_CELL}")

    kept["mean_tenure_years"] = kept["mean_tenure_years"].round(2)
    kept["mean_skill_count"] = kept["mean_skill_count"].round(2)
    return kept


def register(database: str, source_db: str, bucket: str, client_id: str,
             df: pd.DataFrame, rows_in: int) -> None:
    """Register the product with owner and lineage in its table parameters."""
    glue = session().client("glue")

    def gtype(dtype) -> str:
        return {"i": "bigint", "f": "double", "b": "boolean"}.get(dtype.kind, "string")

    location = f"s3://{bucket}/derived/{TABLE}/"
    storage = {
        "Columns": [{"Name": c, "Type": gtype(df[c].dtype)} for c in df.columns],
        "Location": location,
        "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
        "SerdeInfo": {"SerializationLibrary":
                      "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"},
    }
    # These parameters are the governance payload. A consumer reads them
    # instead of asking the team who owns this.
    params = {
        "classification": "parquet",
        "EXTERNAL": "TRUE",
        "ie.tier": "derived",
        "ie.owner": OWNER,
        "ie.lineage.source_table": f"{source_db}.{SOURCE_TABLE}",
        "ie.lineage.built_by": "stages/build_derived.py",
        "ie.lineage.rows_in": str(rows_in),
        "ie.lineage.rows_out": str(len(df)),
        "ie.lineage.built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ie.aggregation": f"department x seniority_level, min cell {MIN_CELL}",
        "ie.contains_personal_data": "false",
        "ie.derived_from_personal_data": "true",
    }
    table_input = {
        "Name": TABLE,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": params,
        "PartitionKeys": [{"Name": "client_id", "Type": "string"}],
        "StorageDescriptor": storage,
    }
    try:
        glue.create_table(DatabaseName=database, TableInput=table_input)
        print(f"created product {database}.{TABLE}")
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(DatabaseName=database, TableInput=table_input)
        print(f"updated product {database}.{TABLE}")

    part = {"Values": [client_id],
            "StorageDescriptor": {**storage, "Location": f"{location}client_id={client_id}/"}}
    try:
        glue.create_partition(DatabaseName=database, TableName=TABLE, PartitionInput=part)
    except glue.exceptions.AlreadyExistsException:
        glue.update_partition(DatabaseName=database, TableName=TABLE,
                              PartitionValueList=[client_id], PartitionInput=part)
    print(f"registered partition client_id={client_id}")


def main(client_id: str) -> None:
    cfg = config()
    lake = cfg["lakehouse_bucket"]

    gate_engagement_permissibility(client_id, purpose="derived product build")

    src = read_parquet(lake, f"foundational/profiles/client_id={client_id}/part-0000.parquet")
    print(f"read {len(src):,} foundational rows through foundational-read")

    product = build(src)

    # The product is aggregate-only, so this gate should find nothing. Running
    # it anyway is the point: the check is on the path, not on the author.
    gate_anonymization(product, tier="derived")

    key = f"derived/{TABLE}/client_id={client_id}/part-0000.parquet"
    written = put_parquet(product, lake, key)
    print(f"wrote s3://{lake}/{key} ({written:,} bytes)")

    register(cfg["derived_db"], cfg["foundational_db"], lake, client_id, product, len(src))

    emit_metric("build_derived", "CellsPublished", float(len(product)), "Count")
    flush_steward_log()
    print(f"DERIVED OK | {len(src):,} rows -> {len(product)} aggregate cells "
          f"| owner {OWNER}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: build_derived.py <client_id>")
    main(sys.argv[1])

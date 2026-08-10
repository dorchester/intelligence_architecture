"""Optional stage: peer benchmarks, computed in Databricks, written to L3.

Every run in this system is scoped to one client by construction, and a
guardrail (`cross_company_isolation`) enforces it. That is the right rule,
and it has a consequence: a report can say *"Clinical is 22% of headcount"*
but never *"...against a 17% peer median"*, because the numbers that would
answer it live in other clients' data.

This stage closes that gap without weakening the rule. The cross-client
aggregation happens **upstream of any run**, in Databricks, over the
already-governed L3 tier; only a suppressed aggregate comes back. A run then
reads the *benchmark product* and never another client's rows - the same
move the medallion model makes everywhere else: the platform builds a
product, the application consumes it.

Why Databricks rather than another Python stage: cross-dataset aggregation
over the whole lakehouse is what a query engine is for, and Unity Catalog
already reads these files in place. The alternative - a stage that lists
every client prefix and reads them all - would need an identity that can
read every client at once, which is exactly the blast radius the tier model
exists to avoid.

Auth is the service principal already in SSM (`databricks/client_id` +
`client_secret`), so no new credential is introduced. Databricks is given
**no write path**: it computes, this stage writes, and the catalog of record
stays in Glue.

**Optional by construction.** If the SSM parameters are absent, the warehouse
is unreachable, or Databricks returns nothing, the stage logs why and exits
0. The report pipeline behaves exactly as it does today. Nothing downstream
may treat a benchmark as required.

    python stages/peer_benchmarks.py            # build the product
    python stages/peer_benchmarks.py --check    # report availability, build nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

import _aws
import _governance

# Small-cell suppression, applied a second time on the aggregate. The L3 input
# is already suppressed; a peer median over very few clients could still be
# re-identifying, so a cell needs this many contributing clients to survive.
MIN_CLIENTS = 3

PRODUCT_PREFIX = "derived/peer_benchmarks"
TABLE = "peer_benchmarks"

# The whole point of the stage, in one statement. It reads the governed L3
# tier through Unity Catalog - never foundational, never a raw drop.
BENCHMARK_SQL = """
SELECT
    department,
    seniority_level,
    COUNT(DISTINCT client_id)                          AS contributing_clients,
    CAST(PERCENTILE(headcount, 0.5)     AS DOUBLE)     AS median_headcount,
    CAST(PERCENTILE(mean_tenure_years, 0.5) AS DOUBLE) AS median_tenure_years,
    CAST(AVG(mean_skill_count)          AS DOUBLE)     AS mean_skill_count
FROM {table}
GROUP BY department, seniority_level
HAVING COUNT(DISTINCT client_id) >= {min_clients}
ORDER BY median_headcount DESC
"""


def _param(ssm, name: str, decrypt: bool = False) -> str | None:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]
    except Exception:
        return None


def databricks_settings() -> dict | None:
    """Resolve Databricks access, or None if this deployment has no Databricks.

    Absence is a normal state, not an error: the substrate is designed to run
    without it.
    """
    env = os.environ.get("IE_ENV", "dev")
    ssm = _aws.session().client("ssm")
    base = f"/intelligence-engine/{env}/databricks"
    settings = {
        "client_id": _param(ssm, f"{base}/client_id"),
        "client_secret": _param(ssm, f"{base}/client_secret", decrypt=True),
        "host": os.environ.get("DATABRICKS_HOST") or _param(ssm, f"{base}/host"),
        "warehouse_id": os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or _param(ssm, f"{base}/warehouse_id"),
        "table": os.environ.get("DATABRICKS_BENCHMARK_TABLE")
        or _param(ssm, f"{base}/benchmark_table"),
    }
    missing = [k for k, v in settings.items() if not v]
    if missing:
        print(f"SKIP | Databricks not configured for this deployment (missing: {', '.join(missing)})")
        return None
    return settings


def _token(settings: dict) -> str:
    """OAuth machine-to-machine for the service principal already in SSM."""
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "all-apis"}
    ).encode()
    req = urllib.request.Request(
        f"{settings['host'].rstrip('/')}/oidc/v1/token", data=body, method="POST"
    )
    import base64

    basic = base64.b64encode(
        f"{settings['client_id']}:{settings['client_secret']}".encode()
    ).decode()
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def run_statement(settings: dict, sql: str, timeout_s: int = 300) -> list[dict]:
    """Execute SQL through the Statement Execution API and return rows."""
    token = _token(settings)
    host = settings["host"].rstrip("/")

    def call(path: str, payload=None, method="GET"):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{host}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    res = call(
        "/api/2.0/sql/statements",
        {
            "statement": sql,
            "warehouse_id": settings["warehouse_id"],
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        },
        method="POST",
    )

    # A cold serverless warehouse takes a moment; poll rather than fail.
    deadline = time.time() + timeout_s
    while res["status"]["state"] in ("PENDING", "RUNNING") and time.time() < deadline:
        time.sleep(5)
        res = call(f"/api/2.0/sql/statements/{res['statement_id']}")

    state = res["status"]["state"]
    if state != "SUCCEEDED":
        raise RuntimeError(f"statement {state}: {res['status'].get('error', {}).get('message', '')}")

    cols = [c["name"] for c in res["manifest"]["schema"]["columns"]]
    return [dict(zip(cols, row)) for row in res.get("result", {}).get("data_array", []) or []]


def register(database: str, bucket: str, rows: int, clients: int) -> None:
    """Catalogue the product with the same lineage vocabulary as every tier."""
    glue = _aws.session().client("glue")
    table = {
        "Name": TABLE,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "classification": "parquet",
            "EXTERNAL": "TRUE",
            "ie.tier": "derived",
            "ie.owner": "workforce-analytics@intelligence-engine.invalid",
            "ie.contains_personal_data": "false",
            "ie.derived_from_personal_data": "true",
            "ie.aggregation": f"department x seniority_level, min {MIN_CLIENTS} contributing clients",
            "ie.lineage.source_table": "derived.workforce_composition (all clients)",
            "ie.lineage.built_by": "stages/peer_benchmarks.py via Databricks SQL",
            "ie.lineage.compute": "databricks-serverless-sql",
            "ie.lineage.rows_out": str(rows),
            "ie.lineage.contributing_clients": str(clients),
            "ie.lineage.built_at": pd.Timestamp.utcnow().isoformat(),
        },
        "StorageDescriptor": {
            "Columns": [
                {"Name": "department", "Type": "string"},
                {"Name": "seniority_level", "Type": "string"},
                {"Name": "contributing_clients", "Type": "bigint"},
                {"Name": "median_headcount", "Type": "double"},
                {"Name": "median_tenure_years", "Type": "double"},
                {"Name": "mean_skill_count", "Type": "double"},
            ],
            "Location": f"s3://{bucket}/{PRODUCT_PREFIX}/",
            "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
            },
        },
    }
    try:
        glue.create_table(DatabaseName=database, TableInput=table)
        print(f"OK | created glue table {database}.{TABLE}")
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(DatabaseName=database, TableInput=table)
        print(f"OK | updated glue table {database}.{TABLE}")


def load(bucket: str, storage=None) -> pd.DataFrame | None:
    """Read the product back. Returns None when no benchmark exists.

    Consumers must treat None as normal - this is the read side of the
    stage's optionality, and a caller that raises on None has made a
    benchmark mandatory, which it is not.
    """
    s3 = (storage or _aws.session()).client("s3")
    key = f"{PRODUCT_PREFIX}/part-0000.parquet"
    try:
        return _aws.read_parquet(bucket, key)
    except Exception:
        return None


def main(check_only: bool = False) -> int:
    cfg = _aws.config()
    lake = cfg["lakehouse_bucket"]

    settings = databricks_settings()
    if settings is None:
        print("OK | pipeline continues without peer benchmarks (optional stage)")
        return 0
    if check_only:
        print(f"OK | Databricks reachable settings present for {settings['host']}")
        return 0

    _governance.gate_engagement_permissibility("*", "cross-client peer benchmark (aggregate only)")

    sql = BENCHMARK_SQL.format(table=settings["table"], min_clients=MIN_CLIENTS)
    print(f"querying Databricks warehouse {settings['warehouse_id']} ...")
    try:
        rows = run_statement(settings, sql)
    except (urllib.error.URLError, RuntimeError, KeyError) as e:
        print(f"SKIP | Databricks query failed ({type(e).__name__}: {e})")
        print("OK | pipeline continues without peer benchmarks (optional stage)")
        return 0

    if not rows:
        print("SKIP | no cell had enough contributing clients to publish")
        return 0

    df = pd.DataFrame(rows)
    for col in ("contributing_clients", "median_headcount", "median_tenure_years", "mean_skill_count"):
        if col in df:
            df[col] = pd.to_numeric(df[col])

    # The aggregate carries no identifiers, but it is still a governed write.
    _governance.gate_anonymization(df, "derived")

    key = f"{PRODUCT_PREFIX}/part-0000.parquet"
    size = _aws.put_parquet(df, lake, key)
    clients = int(df["contributing_clients"].max()) if "contributing_clients" in df else 0
    print(f"OK | wrote s3://{lake}/{key} ({size} bytes, {len(df)} cells, "
          f"up to {clients} contributing clients)")

    register(cfg["derived_db"], lake, len(df), clients)
    _aws.emit_metric("peer_benchmarks", "cells", len(df))
    _governance.flush_steward_log()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report whether Databricks is configured, build nothing")
    a = ap.parse_args()
    sys.exit(main(check_only=a.check))

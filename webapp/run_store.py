"""Durable run state.

Runs persist to DynamoDB at every meaningful transition, which is what makes
checkpoints survivable: a run parked at WAITING_FOR_APPROVAL holds no thread,
no memory that matters, and no compute. The approval - minutes or days later,
before or after a restart or redeploy - rehydrates the run and spawns the
next phase.

Without reachable DynamoDB (local dev, no credentials) the app degrades to
in-memory runs exactly as before. Nothing here raises on policy failure;
persistence problems must never kill an analysis mid-phase.
"""

from __future__ import annotations

import json
from typing import Any

from webapp import runtime

# Keys that must never be persisted: private coordination state.
_STRIP_PREFIX = "_"

# DynamoDB items cap at 400 KB. If a run payload approaches that, the report
# HTML (largest, reproducible field) is dropped from the persisted copy.
_MAX_PAYLOAD_BYTES = 350_000


class RunStore:
    """DynamoDB-backed run persistence with graceful absence."""

    def __init__(self, table_name: str | None = None):
        self._table = None
        self._available = False
        try:
            import os

            name = table_name or os.environ.get("RUN_STATE_TABLE")
            if not name:
                cfn = runtime._session().client("cloudformation")
                resp = cfn.describe_stacks(
                    StackName=f"intelligence-engine-{runtime.ENVIRONMENT}-state"
                )
                name = next(
                    o["OutputValue"]
                    for o in resp["Stacks"][0]["Outputs"]
                    if o["OutputKey"] == "TableName"
                )
            dynamodb = runtime._session().resource("dynamodb")
            table = dynamodb.Table(name)
            table.load()  # cheap existence/credential check
            self._table = table
            self._available = True
        except Exception:
            self._available = False

    def available(self) -> bool:
        return self._available

    # ----- write -----

    def save(self, run: dict) -> None:
        if not self._available:
            return
        try:
            clean = {k: v for k, v in run.items() if not k.startswith(_STRIP_PREFIX)}
            payload = json.dumps(clean, default=str)
            if len(payload) > _MAX_PAYLOAD_BYTES and "report_html" in clean:
                clean = dict(clean)
                clean.pop("report_html", None)
                clean["report_html_dropped"] = True
                payload = json.dumps(clean, default=str)
            self._table.put_item(
                Item={
                    "run_id": run["run_id"],
                    "client_id": run.get("client_ref") or run.get("company_id") or "unknown",
                    "stage": run.get("stage", "unknown"),
                    "company_name": run.get("company_name", ""),
                    "created_at": run.get("created_at", ""),
                    "payload": payload,
                }
            )
        except Exception:
            # Persistence must never take down a run mid-phase.
            pass

    # ----- read -----

    def load(self, run_id: str) -> dict | None:
        if not self._available:
            return None
        try:
            item = self._table.get_item(Key={"run_id": run_id}).get("Item")
            if not item:
                return None
            return json.loads(item["payload"])
        except Exception:
            return None

    def load_all(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent runs, newest first. Scan is fine at this scale."""
        if not self._available:
            return []
        try:
            items = self._table.scan(Limit=limit).get("Items", [])
            out = []
            for item in items:
                try:
                    out.append(json.loads(item["payload"]))
                except Exception:
                    continue
            out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            return out
        except Exception:
            return []

"""The agentic consumption shell: one governed tool over one data product.

    python stages/governed_tool.py <client_id> [department]

The consumption-side twin of the stage-runner contract. An agent does not get
a database connection; it gets **one typed, identity-scoped, read-only tool
whose every call is logged**.

Four properties, each deliberate:

1. **Typed contract.** `TOOL_SPEC` is an MCP-style schema. The agent sees a
   named operation with declared arguments, not SQL. It cannot express a query
   the tool does not offer, so the blast radius is the tool surface rather
   than the data model.
2. **Reads the derived tier only.** The tool is wired to an aggregate product,
   never to `foundational/*`. An agent asking about workforce composition has
   no route to a row about a person - that is an IAM and wiring fact, not a
   prompt instruction.
3. **Identity-scoped.** Every call records the caller identity that STS
   reports, so "which agent read this" is answerable.
4. **Every call logged**, to the stewardship tier, alongside the gate events.

Writes stay deferred. The registry, the marketplace and any agentic write path
are named in the deferred list and deliberately absent - "reads now, writes
later" is a posture the code should make obvious.
"""
from __future__ import annotations

import datetime
import json
import sys
import uuid

from _aws import config, put_bytes, read_parquet, session
from _governance import gate_engagement_permissibility

TOOL_SPEC = {
    "name": "workforce_composition",
    "description": ("Aggregate workforce composition for one client, by "
                    "department and seniority band. Aggregates only - this "
                    "tool cannot return information about an individual."),
    "read_only": True,
    "tier": "derived",
    "input_schema": {
        "type": "object",
        "properties": {
            "client_id": {"type": "string",
                          "description": "Client whose product to read."},
            "department": {"type": "string",
                           "description": "Optional department filter."},
        },
        "required": ["client_id"],
        "additionalProperties": False,
    },
}


def _caller() -> str:
    try:
        return session().client("sts").get_caller_identity()["Arn"].rsplit("/", 1)[-1]
    except Exception:  # noqa: BLE001
        return "unknown"


def _log_call(cfg: dict, args: dict, rows: int, decision: str) -> None:
    """Append one tool-call record to the stewardship tier."""
    rec = {
        "call_id": str(uuid.uuid4()),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tool": TOOL_SPEC["name"],
        "tier": TOOL_SPEC["tier"],
        "caller": _caller(),
        "arguments": args,
        "rows_returned": rows,
        "decision": decision,
    }
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y/%m/%d")
    key = f"stewardship/tool-calls/{stamp}/{rec['call_id']}.json"
    put_bytes(cfg["lakehouse_bucket"], key,
              json.dumps(rec, indent=2).encode(), "application/json")
    print(f"tool call logged -> s3://{cfg['lakehouse_bucket']}/{key}")


def call(client_id: str, department: str | None = None) -> list[dict]:
    """The one operation this tool exposes."""
    cfg = config()

    # Permissibility is checked on the consumption side too. A tool that
    # trusts its caller to have been gated upstream is not gated.
    gate_engagement_permissibility(client_id, purpose=f"tool:{TOOL_SPEC['name']}")

    df = read_parquet(cfg["lakehouse_bucket"],
                      f"derived/workforce_composition/client_id={client_id}/part-0000.parquet")
    if department:
        df = df[df["department"] == department]

    rows = df.to_dict("records")
    _log_call(cfg, {"client_id": client_id, "department": department},
              len(rows), "allowed")
    return rows


def main(client_id: str, department: str | None) -> None:
    print(json.dumps(TOOL_SPEC, indent=2))
    print(f"\ncaller identity: {_caller()}")
    rows = call(client_id, department)
    print(f"\n{len(rows)} aggregate cell(s) returned:")
    for r in rows[:10]:
        print(f"  {str(r.get('department',''))[:34]:36} "
              f"{str(r.get('seniority_level','')):14} "
              f"n={r.get('headcount'):>4}  tenure={r.get('mean_tenure_years')}")
    print("\nTOOL OK | aggregates only | read-only | call logged")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: governed_tool.py <client_id> [department]")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

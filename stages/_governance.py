"""Policy gates and the steward log.

Two pass-through validators and an append-only log. Both are deliberately
minimal: the point is that the enforcement *point* exists and every path runs
through it, so a policy that is agreed later has somewhere to land other than
a code review.

Walls before policies - the same argument as the vault, whose key exists with
no decrypt grants because the wall should precede the data.
"""
from __future__ import annotations

import datetime
import json
import os
import uuid

from _aws import config, put_bytes, session

# Severity ladder. `block` raises; `warn` and `note` record and continue.
BLOCK, WARN, NOTE = "block", "warn", "note"

_events: list[dict] = []


class PolicyViolation(RuntimeError):
    """A blocking gate refused. Deterministic gates decide; models advise."""


def _record(gate: str, severity: str, message: str, detail: dict | None = None) -> dict:
    ev = {
        "event_id": str(uuid.uuid4()),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gate": gate,
        "severity": severity,
        "message": message,
        "detail": detail or {},
        "stage": os.environ.get("IE_STAGE", "unknown"),
        "build": os.environ.get("CODEBUILD_BUILD_ID", "local"),
    }
    _events.append(ev)
    print(f"GATE {severity.upper():5} {gate}: {message}")
    return ev


# ---------------------------------------------------------------- gates

def gate_engagement_permissibility(client_id: str, purpose: str) -> None:
    """(a) MSA/SOW permissibility, checked before an agent reads anything.

    Pass-through stub. The real check asks whether the engagement contract
    permits this client's data to be used for this purpose, which requires a
    contract registry that does not exist yet. What exists now is the call
    site: every read path goes through here, so wiring a registry in later is
    a change in one function rather than an audit of every stage.
    """
    if not client_id:
        raise PolicyViolation("engagement gate: no client_id supplied")
    _record("engagement_permissibility", NOTE,
            f"permissibility assumed for {client_id} / {purpose}",
            {"client_id": client_id, "purpose": purpose,
             "basis": "STUB - no contract registry wired"})


def gate_anonymization(frame, tier: str) -> None:
    """(b) Anonymisation and aggregation, checked before persistence.

    This one does real work already. It refuses to persist a frame carrying a
    direct identifier, and it records - loudly - that dropping identifiers
    leaves the data pseudonymous rather than anonymous. That finding is why
    this gate exists at all; the stub gives it a permanent enforcement point
    instead of a note in a document.
    """
    direct = [c for c in ("first_name", "last_name", "full_name", "headline",
                          "email", "phone", "ssn", "national_id")
              if c in getattr(frame, "columns", [])]
    if direct:
        _record("anonymization", BLOCK,
                f"direct identifiers present in a {tier} write: {direct}",
                {"columns": direct, "tier": tier})
        raise PolicyViolation(
            f"anonymization gate: refusing to persist {direct} into {tier}")

    quasi = [c for c in ("department", "location", "title", "seniority_level",
                         "tenure_years", "profile_id")
             if c in getattr(frame, "columns", [])]
    if len(quasi) >= 3:
        _record("anonymization", WARN,
                "pseudonymous, not anonymous - quasi-identifier combination retained",
                {"quasi_identifiers": quasi, "tier": tier,
                 "implication": "still personal data under GDPR and equivalents"})

    inferred = [c for c in ("turnover_risk_score", "open_to_opportunities")
                if c in getattr(frame, "columns", [])]
    if inferred:
        _record("anonymization", WARN,
                "inferred attributes about individuals retained",
                {"columns": inferred,
                 "implication": "often more sensitive than the identifiers removed"})


# ---------------------------------------------------------- steward log

def flush_steward_log(notify: bool = True) -> str | None:
    """Append this run's gate outcomes to the stewardship tier.

    Append-only by convention and by grant: the stewardship policy carries
    PutObject and nothing else, so a stage cannot rewrite history it dislikes.
    """
    if not _events:
        print("steward log: no events")
        return None

    cfg = config()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y/%m/%d/%H%M%S")
    key = f"stewardship/gate-events/{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
    body = "\n".join(json.dumps(e) for e in _events).encode()
    put_bytes(cfg["lakehouse_bucket"], key, body, "application/x-ndjson")
    print(f"steward log: {len(_events)} event(s) -> s3://{cfg['lakehouse_bucket']}/{key}")

    escalations = [e for e in _events if e["severity"] in (BLOCK, WARN)]
    if notify and escalations and cfg.get("steward_topic_arn"):
        lines = [f"{e['severity'].upper():5} {e['gate']}: {e['message']}"
                 for e in escalations]
        session().client("sns").publish(
            TopicArn=cfg["steward_topic_arn"],
            Subject=f"Stewardship digest: {len(escalations)} item(s) for review",
            Message=("Gate outcomes requiring a steward's attention.\n\n"
                     + "\n".join(lines)
                     + f"\n\nFull log: s3://{cfg['lakehouse_bucket']}/{key}\n"),
        )
        print(f"steward digest published for {len(escalations)} escalation(s)")
    return key

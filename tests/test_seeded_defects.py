"""Eval-bar stub: does the semantic reviewer still catch planted defects?

The golden replay runs pytest, which tests code. It says nothing about whether
a *model* still behaves. That gap matters because a reviewer degrades
silently: nothing errors, findings just stop appearing, and the first symptom
is a defect reaching a client.

Three planted defects, one per failure class the reviewer exists to catch:

  arithmetic     a stated difference that does not match its own inputs
  unsupported    a figure with no basis in the data provided
  contradiction  two claims in one summary that cannot both be true

Scored as recall - how many planted defects the reviewer names - because a
reviewer that catches two of three has degraded even while still "working".

Skipped without credentials, so it never blocks local development. In the
in-account golden replay it runs for real, which is the point: the eval needs
the same data boundary as the workload.
"""
from __future__ import annotations

import json
import os

import pytest

# The bar. Below this the reviewer has regressed enough to investigate before
# shipping, even if every other test passes.
MIN_RECALL = 2 / 3

SEEDED = [
    {
        "id": "arithmetic",
        "counts": {"Clinical": 107, "Commercial": 88},
        "summary": "Clinical exceeds Commercial by 22 roles, the largest gap in the organisation.",
        "defect": "107 - 88 is 19, not 22",
    },
    {
        "id": "unsupported",
        "counts": {"Regulatory": 85, "Manufacturing": 110},
        "summary": "Regulatory attrition is running at 14% year on year and accelerating.",
        "defect": "no attrition figure appears in the counts provided",
    },
    {
        "id": "contradiction",
        "counts": {"Technology": 60, "Corporate Functions": 50},
        "summary": ("Technology is the smallest function in the organisation, "
                    "and at 60 roles it is larger than Corporate Functions at 50."),
        "defect": "cannot be both the smallest and larger than another function",
    },
]

REVIEW_SYSTEM = """You are an advisory reviewer checking a summary against the
counts it claims to describe. Quote the exact text you object to. Reply with
JSON only: {"concerns": [{"quote": "...", "why": "..."}]}"""


def _reviewer_available() -> bool:
    if os.environ.get("IE_SKIP_MODEL_EVALS"):
        return False
    try:
        import boto3
        boto3.Session().client("sts").get_caller_identity()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _reviewer_available(),
                    reason="no AWS credentials; model evals run in the in-account replay")
def test_reviewer_catches_seeded_defects():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stages"))
    import _bedrock  # noqa: PLC0415

    caught, missed = [], []
    for case in SEEDED:
        user = (f"COUNTS:\n{json.dumps(case['counts'], indent=1)}\n\n"
                f"SUMMARY:\n{case['summary']}")
        result = _bedrock.invoke_json(REVIEW_SYSTEM, user, max_tokens=800)
        concerns = result.get("concerns", []) if isinstance(result, dict) else []
        (caught if concerns else missed).append(case["id"])

    recall = len(caught) / len(SEEDED)
    print(f"reviewer recall {recall:.0%} - caught {caught}, missed {missed}")
    assert recall >= MIN_RECALL, (
        f"reviewer recall {recall:.0%} is below the {MIN_RECALL:.0%} bar; "
        f"missed {missed}. Investigate before shipping - a reviewer that "
        f"degrades quietly is worse than no reviewer."
    )


def test_seeded_suite_is_well_formed():
    """Runs everywhere. Guards the fixtures themselves against rot."""
    assert len(SEEDED) == 3
    assert {c["id"] for c in SEEDED} == {"arithmetic", "unsupported", "contradiction"}
    for case in SEEDED:
        assert case["summary"] and case["defect"] and case["counts"]

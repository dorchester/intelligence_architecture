"""Shared runtime state and service accessors.

Both the consultant console and the engineer console read from here.
Keeping this separate lets the two consoles live in different modules
(and, if needed later, different processes) without a circular import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

RUNS_DIR = Path(os.environ.get("RUNS_DIR", BASE_DIR / "runs"))

from guardrails.engine import GuardrailEngine  # noqa: E402

# Default runtime model for the Intelligence Engine (distinct from the
# model Claude Code uses for engineering).
DEFAULT_MODEL = os.environ.get("ENGINE_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# On a laptop, credentials come from a named SSO profile. In a container they
# come from the App Runner instance role, and there is no profile at all —
# boto3 must be allowed to fall through to the default chain.
AWS_PROFILE = os.environ.get("AWS_PROFILE_NAME") or None
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
STORAGE_STACK = f"intelligence-engine-{ENVIRONMENT}-storage"

# When deployed, the bucket is injected directly so the task role does not
# need cloudformation:DescribeStacks just to find its own bucket.
RUNS_BUCKET = os.environ.get("RUNS_BUCKET") or None


def _session(**kwargs):
    """boto3 Session honouring the profile only when one is configured."""
    import boto3

    if AWS_PROFILE:
        kwargs["profile_name"] = AWS_PROFILE
    return boto3.Session(region_name=AWS_REGION, **kwargs)


def is_deployed() -> bool:
    """True when running as the deployed App Runner service."""
    return bool(os.environ.get("DEPLOYED"))

# In-memory run registry. Production would back this with DynamoDB.
runs: dict[str, dict] = {}

# Guardrail engine — reloadable so engineers can tune without a restart.
guardrails = GuardrailEngine()

_dataset_query = None
_dataset_error: str | None = None


def get_bedrock_model(model_id: str = DEFAULT_MODEL):
    from agent.model import BedrockModel, ModelConfig

    return BedrockModel(ModelConfig(model_id=model_id, profile=AWS_PROFILE))


def dataset_error() -> str | None:
    return _dataset_error


def resolve_bucket() -> str:
    """Bucket name from the environment, falling back to the stack output."""
    if RUNS_BUCKET:
        return RUNS_BUCKET
    cfn = _session().client("cloudformation")
    resp = cfn.describe_stacks(StackName=STORAGE_STACK)
    return next(
        o["OutputValue"]
        for o in resp["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "BucketName"
    )


def get_dataset_query():
    """Return a DatasetQuery bound to the project bucket, or None."""
    global _dataset_query, _dataset_error
    if _dataset_query is not None:
        return _dataset_query
    try:
        from datasets.query import DatasetQuery

        bucket = resolve_bucket()
        limits = guardrails.dataset_limits()
        _dataset_query = DatasetQuery(
            bucket=bucket,
            region=AWS_REGION,
            profile=AWS_PROFILE,
            max_profiles=limits["max_profiles_returned"],
            max_postings=limits["max_postings_returned"],
        )
        _dataset_error = None
        return _dataset_query
    except Exception as e:
        _dataset_error = f"{type(e).__name__}: {e}"
        return None


def reset_dataset_client() -> None:
    """Drop the cached client so the next call re-resolves the bucket.

    Useful after refreshing AWS credentials.
    """
    global _dataset_query, _dataset_error
    _dataset_query = None
    _dataset_error = None


def list_preset_companies() -> list[dict]:
    """Companies with a synthetic workforce dataset available in S3."""
    dq = get_dataset_query()
    if not dq:
        return []
    try:
        return dq.list_companies()
    except Exception:
        return []


def active_run_count() -> int:
    terminal = ("completed", "rejected", "error", "blocked")
    return sum(1 for r in runs.values() if r["stage"] not in terminal)

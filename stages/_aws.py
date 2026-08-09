"""Shared AWS resolution and IO for reference stages.

Stages resolve everything from one enumerated SSM path rather than from
hardcoded names, so the same code runs locally, in CodeBuild, and in a
different account without edits. See docs/integration-contract.md 1.7.
"""
from __future__ import annotations

import io
import json
import os
import boto3

ENVIRONMENT = os.environ.get("IE_ENV", "dev")
CONFIG_PARAM = f"/intelligence-engine/{ENVIRONMENT}/stages/demo-config"

_session = None
_config = None


def session():
    """One session per process.

    AWS_PROFILE_NAME lets a laptop use a named SSO profile; in CodeBuild the
    role is ambient and no profile exists, so it must stay unset there.
    """
    global _session
    if _session is None:
        profile = os.environ.get("AWS_PROFILE_NAME")
        kwargs = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
        if profile:
            kwargs["profile_name"] = profile
        _session = boto3.Session(**kwargs)
    return _session


def config() -> dict:
    """Stage configuration, read once from SSM (SecureString-aware)."""
    global _config
    if _config is None:
        raw = session().client("ssm").get_parameter(
            Name=CONFIG_PARAM, WithDecryption=True
        )["Parameter"]["Value"]
        _config = json.loads(raw)
    return _config


def read_jsonl(bucket: str, key: str) -> list[dict]:
    body = session().client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return [json.loads(line) for line in body.decode().splitlines() if line.strip()]


def read_parquet(bucket: str, key: str):
    """Read a parquet object into a DataFrame.

    Fetched through boto3 rather than an s3:// path so the stage needs only
    the enumerated GetObject grant - no s3fs, no bucket-level listing.
    """
    import pandas as pd
    body = session().client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return pd.read_parquet(io.BytesIO(body))


def put_bytes(bucket: str, key: str, data: bytes, content_type: str) -> None:
    session().client("s3").put_object(
        Bucket=bucket, Key=key, Body=data, ContentType=content_type
    )


def put_json(bucket: str, key: str, obj) -> None:
    put_bytes(bucket, key, json.dumps(obj, indent=2).encode(), "application/json")


def put_parquet(df, bucket: str, key: str) -> int:
    """Write a DataFrame as parquet. Returns bytes written."""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="snappy")
    data = buf.getvalue()
    put_bytes(bucket, key, data, "application/octet-stream")
    return len(data)


def emit_metric(stage: str, metric: str, value: float, unit: str = "None") -> None:
    """One EMF line per stage.

    CloudWatch parses embedded-metric-format JSON out of the log stream, so a
    stage gets a real metric without any PutMetricData permission - which the
    stage role deliberately does not have.
    """
    print(json.dumps({
        "_aws": {
            "Timestamp": int(__import__("time").time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "IntelligenceEngine/Stages",
                "Dimensions": [["Stage", "ImageTag"]],
                "Metrics": [{"Name": metric, "Unit": unit}],
            }],
        },
        "Stage": stage,
        "ImageTag": os.environ.get("IMAGE_TAG", "unknown"),
        metric: value,
    }))

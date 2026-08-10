"""Import and run the peer_cohort_shape notebook on Databricks serverless.

Reads the same SSM parameters as stages/peer_benchmarks.py, imports the
notebook via the Workspace API, submits a one-time serverless Jobs API run,
polls to completion, and prints the outcome.

Exits 0 in all cases: if Databricks is not configured, the script prints
SKIP and succeeds — the pipeline does not depend on this analysis.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NOTEBOOK_PATH = "/intelligence-engine/notebooks/peer_cohort_shape"
LOCAL_NOTEBOOK = Path(__file__).resolve().parent.parent / "notebooks" / "peer_cohort_shape.py"

ENVIRONMENT = os.environ.get("IE_ENV", "dev")
SSM_BASE = f"/intelligence-engine/{ENVIRONMENT}/databricks"


def _ssm_client():
    import boto3
    profile = os.environ.get("AWS_PROFILE_NAME")
    kwargs = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs).client("ssm")


def _param(ssm, name: str, decrypt: bool = False) -> str | None:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]
    except Exception:
        return None


def databricks_settings() -> dict | None:
    ssm = _ssm_client()
    settings = {
        "client_id": _param(ssm, f"{SSM_BASE}/client_id"),
        "client_secret": _param(ssm, f"{SSM_BASE}/client_secret", decrypt=True),
        "host": os.environ.get("DATABRICKS_HOST") or _param(ssm, f"{SSM_BASE}/host"),
        "warehouse_id": os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or _param(ssm, f"{SSM_BASE}/warehouse_id"),
    }
    missing = [k for k, v in settings.items() if not v]
    if missing:
        print(f"SKIP | Databricks not configured (missing: {', '.join(missing)})")
        return None
    return settings


def _lakehouse_bucket() -> str | None:
    """Resolve the lakehouse bucket from SSM stage config (same source as _aws.config)."""
    import boto3
    profile = os.environ.get("AWS_PROFILE_NAME")
    kwargs = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
    if profile:
        kwargs["profile_name"] = profile
    ssm = boto3.Session(**kwargs).client("ssm")
    config_param = f"/intelligence-engine/{ENVIRONMENT}/stages/demo-config"
    try:
        raw = ssm.get_parameter(Name=config_param, WithDecryption=True)["Parameter"]["Value"]
        return json.loads(raw).get("lakehouse_bucket")
    except Exception:
        return None


def _token(settings: dict) -> str:
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "all-apis"}
    ).encode()
    req = urllib.request.Request(
        f"{settings['host'].rstrip('/')}/oidc/v1/token", data=body, method="POST"
    )
    basic = base64.b64encode(
        f"{settings['client_id']}:{settings['client_secret']}".encode()
    ).decode()
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def _api(host: str, token: str, method: str, path: str, payload=None):
    url = f"{host.rstrip('/')}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def import_notebook(host: str, token: str) -> None:
    parent = str(Path(NOTEBOOK_PATH).parent)
    _api(host, token, "POST", "/api/2.0/workspace/mkdirs", {"path": parent})
    content = LOCAL_NOTEBOOK.read_text()
    encoded = base64.b64encode(content.encode()).decode()
    _api(host, token, "POST", "/api/2.0/workspace/import", {
        "path": NOTEBOOK_PATH,
        "language": "PYTHON",
        "overwrite": True,
        "format": "SOURCE",
        "content": encoded,
    })
    print(f"OK | imported notebook to {NOTEBOOK_PATH}")


def submit_run(host: str, token: str, lakehouse_bucket: str) -> str:
    result = _api(host, token, "POST", "/api/2.1/jobs/runs/submit", {
        "run_name": "peer-cohort-shape-analysis",
        "tasks": [{
            "task_key": "shape_analysis",
            "notebook_task": {
                "notebook_path": NOTEBOOK_PATH,
                "base_parameters": {
                    "lakehouse_s3_prefix": f"s3://{lakehouse_bucket}",
                },
            },
            "environment_key": "default",
        }],
        "environments": [{
            "environment_key": "default",
            "spec": {
                "client": "2",
                "dependencies": ["numpy"],
            },
        }],
    })
    run_id = result["run_id"]
    print(f"OK | submitted run {run_id}")
    return run_id


def poll_run(host: str, token: str, run_id: str, timeout_s: int = 600) -> dict:
    deadline = time.time() + timeout_s
    terminal_states = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}
    while time.time() < deadline:
        result = _api(host, token, "GET", f"/api/2.1/jobs/runs/get?run_id={run_id}")
        state = result["state"]["life_cycle_state"]
        if state in terminal_states:
            return result
        print(f"  ... {state}")
        time.sleep(10)
    raise TimeoutError(f"run {run_id} did not complete within {timeout_s}s")


def main() -> int:
    settings = databricks_settings()
    if settings is None:
        return 0

    lakehouse_bucket = _lakehouse_bucket()
    if not lakehouse_bucket:
        print("SKIP | could not resolve lakehouse_bucket from SSM stage config")
        return 0

    if not LOCAL_NOTEBOOK.exists():
        print(f"ERROR | notebook not found at {LOCAL_NOTEBOOK}")
        return 1

    try:
        token = _token(settings)
    except (urllib.error.URLError, KeyError) as e:
        print(f"SKIP | could not authenticate to Databricks ({type(e).__name__}: {e})")
        return 0

    host = settings["host"]

    try:
        import_notebook(host, token)
        run_id = submit_run(host, token, lakehouse_bucket)
        result = poll_run(host, token, run_id)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"SKIP | Databricks run failed ({type(e).__name__}: {e})")
        return 0

    result_state = result["state"].get("result_state", "UNKNOWN")
    message = result["state"].get("state_message", "")
    if result_state == "SUCCESS":
        print(f"OK | run {run_id} completed successfully")
    else:
        print(f"WARN | run {run_id} finished with result_state={result_state}: {message}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

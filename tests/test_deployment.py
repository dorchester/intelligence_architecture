"""Tests for the hosted-deployment surface.

These cover the things that differ between a laptop and the container: how
credentials and configuration are resolved, whether auth engages, and whether
the infrastructure templates leak anything that must not be public.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CFN_DIR = BASE_DIR / "infrastructure" / "cloudformation"


# ---------- CloudFormation ----------

class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation's short-form intrinsics."""


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_Loader.add_multi_constructor("!", _any_tag)


def _templates() -> list[Path]:
    return sorted(CFN_DIR.glob("*.yaml"))


def test_every_template_parses():
    assert _templates(), "no CloudFormation templates found"
    for path in _templates():
        with path.open(encoding="utf-8") as fh:
            doc = yaml.load(fh, Loader=_Loader)
        assert doc.get("Resources"), f"{path.name} declares no resources"


def test_no_account_id_in_templates():
    """Account IDs must be resolved at deploy time, never committed."""
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for token in text.split():
            digits = token.strip("\"'/:,")
            if digits.isdigit() and len(digits) == 12:
                pytest.fail(f"{path.name} contains a literal 12-digit account id")


def test_app_runner_pinned_to_one_instance():
    """Run state is in-process; a second instance would not see it."""
    with (CFN_DIR / "app.yaml").open(encoding="utf-8") as fh:
        doc = yaml.load(fh, Loader=_Loader)
    scaling = doc["Resources"]["AutoScalingConfig"]["Properties"]
    assert int(scaling["MaxSize"]) == 1


def test_instance_role_grants_no_wildcard_actions():
    with (CFN_DIR / "app.yaml").open(encoding="utf-8") as fh:
        doc = yaml.load(fh, Loader=_Loader)
    policies = doc["Resources"]["AppRunnerInstanceRole"]["Properties"]["Policies"]
    for policy in policies:
        for stmt in policy["PolicyDocument"]["Statement"]:
            actions = stmt["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            for action in actions:
                assert action != "*", f"{policy['PolicyName']} grants *"
                assert not action.endswith(":*"), f"{policy['PolicyName']} grants {action}"


def test_bucket_stays_private():
    with (CFN_DIR / "storage.yaml").open(encoding="utf-8") as fh:
        doc = yaml.load(fh, Loader=_Loader)
    block = doc["Resources"]["RunBucket"]["Properties"]["PublicAccessBlockConfiguration"]
    assert all(block.values())


# ---------- container ----------

def test_dockerfile_runs_as_non_root():
    text = (BASE_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "USER engine" in text


def test_gunicorn_stays_single_worker():
    """A second worker would not share the in-process run registry."""
    from webapp import gunicorn_conf

    assert gunicorn_conf.workers == 1
    assert gunicorn_conf.threads > 1, "concurrency comes from threads, not workers"


def test_health_check_excluded_from_access_log():
    """App Runner polls /healthz every 20s; logging it is 4,320 lines a day."""
    import logging

    from webapp.gunicorn_conf import _SkipHealthCheck

    f = _SkipHealthCheck()

    def rec(msg):
        return logging.LogRecord("gunicorn.access", logging.INFO, "", 0, msg, None, None)

    assert f.filter(rec('GET /healthz HTTP/1.1" 200')) is False
    assert f.filter(rec('GET /engineer/ HTTP/1.1" 200')) is True


# ---------- runtime configuration ----------

def _reload_runtime(monkeypatch, **env):
    for key in ("AWS_PROFILE_NAME", "AWS_PROFILE", "RUNS_BUCKET", "ENVIRONMENT", "DEPLOYED"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import webapp.runtime as rt

    return importlib.reload(rt)


def test_container_uses_instance_role_not_profile(monkeypatch):
    """When DEPLOYED, no named profile — boto3 must use the instance role."""
    rt = _reload_runtime(monkeypatch, DEPLOYED="1")
    assert rt.AWS_PROFILE is None
    assert rt.is_deployed() is True


def test_local_dev_defaults_to_project_profile(monkeypatch):
    """On a laptop the SSO profile must engage without any env setup —
    this regressed once and silently broke every local preset run."""
    rt = _reload_runtime(monkeypatch)
    assert rt.AWS_PROFILE == "intelligence-dev"
    assert rt.is_deployed() is False


def test_profile_used_when_set(monkeypatch):
    rt = _reload_runtime(monkeypatch, AWS_PROFILE_NAME="intelligence-dev")
    assert rt.AWS_PROFILE == "intelligence-dev"


def test_bucket_from_env_avoids_cloudformation(monkeypatch):
    """The task role should not need DescribeStacks to find its own bucket."""
    rt = _reload_runtime(monkeypatch, RUNS_BUCKET="some-bucket", DEPLOYED="1")
    assert rt.resolve_bucket() == "some-bucket"
    assert rt.is_deployed() is True


def test_environment_selects_stack_names(monkeypatch):
    rt = _reload_runtime(monkeypatch, ENVIRONMENT="staging")
    assert rt.STORAGE_STACK == "intelligence-engine-staging-storage"
    _reload_runtime(monkeypatch)  # restore


# ---------- authentication ----------

def test_auth_inert_without_cognito(monkeypatch):
    for key in ("COGNITO_DOMAIN", "COGNITO_CLIENT_ID"):
        monkeypatch.delenv(key, raising=False)
    from webapp import auth

    assert auth.auth_enabled() is False


def test_auth_enabled_with_cognito(monkeypatch):
    monkeypatch.setenv("COGNITO_DOMAIN", "example.auth.us-east-1.amazoncognito.com")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "abc123")
    from webapp import auth

    assert auth.auth_enabled() is True


def test_claim_decoding_survives_garbage():
    from webapp.auth import _decode_claims

    assert _decode_claims("not-a-jwt") == {}
    assert _decode_claims("") == {}


def test_health_check_needs_no_session():
    """App Runner polls /healthz unauthenticated; it must never redirect."""
    from webapp.auth import PUBLIC_PATHS

    assert "/healthz" in PUBLIC_PATHS
    assert "/auth/callback" in PUBLIC_PATHS


def test_healthz_responds():
    os.environ.pop("COGNITO_DOMAIN", None)
    from webapp.app import app

    with app.test_client() as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


def test_guardrail_edits_are_disabled_when_deployed(monkeypatch):
    """Enforcement config must not be editable from a browser in production.

    Hot reloading is a good local affordance. Deployed, it changes what the
    system blocks with no diff, no review and no author - which contradicts
    the rule every other change in this project follows.
    """
    monkeypatch.setenv("DEPLOYED", "1")
    import webapp.engineer as engineer

    importlib.reload(engineer)
    assert engineer.EDITS_ALLOWED is False, (
        "guardrail editing must be read-only when DEPLOYED=1"
    )

    monkeypatch.delenv("DEPLOYED", raising=False)
    importlib.reload(engineer)
    assert engineer.EDITS_ALLOWED is True, "local development keeps the editor"

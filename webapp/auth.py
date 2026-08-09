"""Cognito authentication for the deployed console.

The App Runner URL is public, so the service is fronted by a Cognito hosted
login. This module is inert unless the Cognito environment variables are
present, which means local development is unchanged: no login, no config.

Flow is the standard OAuth2 authorization code grant. The client secret is
never stored in the repository or in a CloudFormation output — it is read at
boot from the user pool with the instance role's
`cognito-idp:DescribeUserPoolClient` permission.

Note on token validation: the ID token is not signature-verified here because
it is received directly from Cognito's token endpoint over TLS in exchange for
a single-use code, rather than from the browser. That is the standard
confidential-client position. If tokens ever start arriving via the front
channel (implicit flow, or an ALB passing headers), this must become a real
JWKS verification.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.parse
import urllib.request
from functools import wraps

from flask import Flask, redirect, request, session, url_for

# Paths reachable without a session.
PUBLIC_PATHS = ("/auth/login", "/auth/callback", "/healthz", "/static")

_client_secret: str | None = None


def auth_enabled() -> bool:
    return bool(os.environ.get("COGNITO_DOMAIN") and os.environ.get("COGNITO_CLIENT_ID"))


def _cfg() -> dict:
    return {
        "domain": os.environ.get("COGNITO_DOMAIN", ""),
        "client_id": os.environ.get("COGNITO_CLIENT_ID", ""),
        "user_pool_id": os.environ.get("COGNITO_USER_POOL_ID", ""),
        "region": os.environ.get("AWS_REGION", "us-east-1"),
    }


def _get_client_secret() -> str | None:
    """Read the app client secret from Cognito itself, once."""
    global _client_secret
    if _client_secret is not None:
        return _client_secret or None

    cfg = _cfg()
    if not cfg["user_pool_id"]:
        _client_secret = ""
        return None
    try:
        import boto3

        client = boto3.Session(region_name=cfg["region"]).client("cognito-idp")
        resp = client.describe_user_pool_client(
            UserPoolId=cfg["user_pool_id"], ClientId=cfg["client_id"]
        )
        _client_secret = resp["UserPoolClient"].get("ClientSecret", "")
    except Exception:
        # A pool configured without a secret is valid; so is a transient
        # failure. Either way we fall back to a public-client exchange.
        _client_secret = ""
    return _client_secret or None


def _redirect_uri() -> str:
    configured = os.environ.get("COGNITO_REDIRECT_URI")
    if configured:
        return configured
    # App Runner terminates TLS upstream, so request.url_root can say http.
    root = request.url_root.replace("http://", "https://")
    return root.rstrip("/") + "/auth/callback"


def _hosted_ui_url(path: str, **params) -> str:
    cfg = _cfg()
    return f"https://{cfg['domain']}/{path}?" + urllib.parse.urlencode(params)


def init_auth(app: Flask) -> None:
    """Register auth routes and the session guard.

    Safe to call unconditionally — does nothing when Cognito is not configured.
    """
    if not auth_enabled():
        return

    @app.route("/auth/login")
    def auth_login():
        state = secrets.token_urlsafe(16)
        session["oauth_state"] = state
        return redirect(
            _hosted_ui_url(
                "oauth2/authorize",
                client_id=_cfg()["client_id"],
                response_type="code",
                scope="openid email profile",
                redirect_uri=_redirect_uri(),
                state=state,
            )
        )

    @app.route("/auth/callback")
    def auth_callback():
        if request.args.get("state") != session.pop("oauth_state", None):
            return "Invalid state", 400
        code = request.args.get("code")
        if not code:
            return "No authorization code", 400

        cfg = _cfg()
        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": cfg["client_id"],
                "code": code,
                "redirect_uri": _redirect_uri(),
            }
        ).encode()

        req = urllib.request.Request(
            f"https://{cfg['domain']}/oauth2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        client_secret = _get_client_secret()
        if client_secret:
            basic = base64.b64encode(
                f"{cfg['client_id']}:{client_secret}".encode()
            ).decode()
            req.add_header("Authorization", f"Basic {basic}")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                tokens = json.loads(resp.read())
        except Exception as e:
            return f"Token exchange failed: {type(e).__name__}", 502

        claims = _decode_claims(tokens.get("id_token", ""))
        session["user"] = claims.get("email") or claims.get("cognito:username") or "user"
        session.permanent = True
        return redirect(url_for("index"))

    @app.route("/auth/logout")
    def auth_logout():
        session.clear()
        cfg = _cfg()
        root = request.url_root.replace("http://", "https://").rstrip("/")
        return redirect(
            _hosted_ui_url("logout", client_id=cfg["client_id"], logout_uri=root)
        )

    @app.before_request
    def require_login():
        if request.path.startswith(PUBLIC_PATHS):
            return None
        if session.get("user"):
            return None
        return redirect(url_for("auth_login"))


def _decode_claims(id_token: str) -> dict:
    """Read the payload of a JWT without verifying it. See module docstring."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def login_required(fn):
    """Decorator for routes that must never be reachable anonymously."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if auth_enabled() and not session.get("user"):
            return redirect(url_for("auth_login"))
        return fn(*args, **kwargs)

    return wrapper

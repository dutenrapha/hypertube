"""
CARD 05 — OAuth2 via 42 School

Tests the login-via-42 implementation as it is today:
  - Backend: GET /api/auth/oauth/42 (redirect to 42), GET /api/auth/oauth/42/callback (exchange code, create/link user, redirect with JWT)
  - Frontend: Login page has "Login with 42" link to /api/auth/oauth/42; AuthCallback page reads ?token= and completes login

These tests run against the backend without mock: they cover the redirect to 42,
callback error handling (missing code, invalid code), and that the client secret
is never leaked. Successful callback flow (valid code → JWT redirect) would require
a real 42 authorization or a mock and is not covered here.

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card05.py -v
"""

import os
import urllib.parse

import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_URL = "http://localhost:8000"
OAUTH42_URL = f"{BACKEND_URL}/api/auth/oauth/42"
CALLBACK_URL = f"{BACKEND_URL}/api/auth/oauth/42/callback"


def _load_env() -> dict:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    cfg: dict = {}
    if not os.path.isfile(env_path):
        return cfg
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip()
    return cfg


_env = _load_env()
OAUTH_42_CLIENT_ID = _env.get("OAUTH_42_CLIENT_ID", "")
OAUTH_42_REDIRECT_URI = _env.get("OAUTH_42_REDIRECT_URI", "")
OAUTH_42_CLIENT_SECRET = _env.get("OAUTH_42_CLIENT_SECRET", "")


# ---------------------------------------------------------------------------
# GET /api/auth/oauth/42 — redirect to 42
# ---------------------------------------------------------------------------

def test_oauth42_redirect_returns_302_to_intra42():
    """GET /api/auth/oauth/42 returns 302 with Location to intra.42.fr/oauth/authorize."""
    resp = requests.get(OAUTH42_URL, allow_redirects=False, timeout=10)
    assert resp.status_code in (301, 302, 303, 307, 308), (
        f"Expected a redirect, got {resp.status_code}: {resp.text}"
    )
    location = resp.headers.get("location", "")
    assert "intra.42.fr/oauth/authorize" in location, (
        f"Expected 42 auth URL in Location, got: {location}"
    )


def test_oauth42_redirect_includes_required_params():
    """Redirect URL includes client_id, redirect_uri and response_type=code."""
    resp = requests.get(OAUTH42_URL, allow_redirects=False, timeout=10)
    assert resp.status_code in (301, 302, 303, 307, 308)
    location = resp.headers.get("location", "")
    assert "client_id=" in location
    assert "redirect_uri=" in location
    assert "response_type=code" in location
    if OAUTH_42_CLIENT_ID:
        assert OAUTH_42_CLIENT_ID in location or urllib.parse.quote(OAUTH_42_CLIENT_ID, safe="") in location
    if OAUTH_42_REDIRECT_URI:
        # redirect_uri is URL-encoded in the query
        encoded = urllib.parse.quote(OAUTH_42_REDIRECT_URI, safe="")
        assert encoded in location or OAUTH_42_REDIRECT_URI in location


# ---------------------------------------------------------------------------
# GET /api/auth/oauth/42/callback — error cases
# ---------------------------------------------------------------------------

def test_callback_missing_code_returns_400():
    """Callback without code parameter returns 400 and JSON error missing_code."""
    resp = requests.get(CALLBACK_URL, allow_redirects=False, timeout=10)
    assert resp.status_code == 400, (
        f"Expected 400 when code is missing, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert data.get("error") == "missing_code", (
        f"Expected error 'missing_code', got: {data}"
    )


def test_callback_invalid_code_returns_error():
    """Callback with an invalid or expired code returns 400 or 502 and JSON error (42 rejects or unreachable)."""
    resp = requests.get(
        CALLBACK_URL,
        params={"code": "invalid_or_expired_fake_code_42"},
        allow_redirects=False,
        timeout=10,
    )
    assert resp.status_code in (400, 502), (
        f"Expected 400 or 502 for invalid/expired code, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert "error" in data
    assert data["error"] in (
        "invalid_or_expired_code",
        "token_exchange_failed",
        "token_parse_failed",
        "missing_code",
    ), f"Expected error in callback response, got: {data}"


# ---------------------------------------------------------------------------
# Security: client secret never in responses
# ---------------------------------------------------------------------------

def test_oauth42_client_secret_not_in_redirect_response():
    """OAUTH_42_CLIENT_SECRET must not appear in the redirect to 42."""
    if not OAUTH_42_CLIENT_SECRET:
        pytest.skip("OAUTH_42_CLIENT_SECRET not set in .env")
    resp = requests.get(OAUTH42_URL, allow_redirects=False, timeout=10)
    assert OAUTH_42_CLIENT_SECRET not in resp.text
    assert OAUTH_42_CLIENT_SECRET not in str(resp.headers)


def test_oauth42_client_secret_not_in_callback_error_response():
    """OAUTH_42_CLIENT_SECRET must not appear in callback error responses."""
    if not OAUTH_42_CLIENT_SECRET:
        pytest.skip("OAUTH_42_CLIENT_SECRET not set in .env")
    resp = requests.get(
        CALLBACK_URL,
        params={"code": "any_fake_code"},
        allow_redirects=False,
        timeout=10,
    )
    assert OAUTH_42_CLIENT_SECRET not in resp.text
    assert OAUTH_42_CLIENT_SECRET not in str(resp.headers)

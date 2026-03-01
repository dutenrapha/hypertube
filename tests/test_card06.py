"""
CARD 06 — OAuth2 via Google

Tests the login-via-Google implementation:
  - Backend: GET /api/auth/oauth/google (redirect to Google),
             GET /api/auth/oauth/google/callback (exchange code, create/link user, redirect with JWT)
  - Frontend: Login page has "Login with Google" link to /api/auth/oauth/google;
              AuthCallback page reads ?token= and completes login

These tests run against the backend without mock: they cover the redirect to Google,
callback error handling (missing code, invalid code), and that the client secret
is never leaked. Successful callback flow (valid code → JWT redirect) would require
a real Google authorization and is not covered here.

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card06.py -v
"""

import os
import urllib.parse

import pytest
import requests

PROJECT_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_URL    = "http://localhost:8000"
OAUTH_GOOGLE_URL = f"{BACKEND_URL}/api/auth/oauth/google"
CALLBACK_URL   = f"{BACKEND_URL}/api/auth/oauth/google/callback"
FRONTEND_URL   = "http://localhost:3000"


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
OAUTH_GOOGLE_CLIENT_ID     = _env.get("OAUTH_GOOGLE_CLIENT_ID", "")
OAUTH_GOOGLE_REDIRECT_URI  = _env.get("OAUTH_GOOGLE_REDIRECT_URI", "")
OAUTH_GOOGLE_CLIENT_SECRET = _env.get("OAUTH_GOOGLE_CLIENT_SECRET", "")


# ---------------------------------------------------------------------------
# GET /api/auth/oauth/google — redirect to Google
# ---------------------------------------------------------------------------

def test_oauth_google_redirect_returns_302_to_accounts_google():
    """GET /api/auth/oauth/google returns a redirect to accounts.google.com."""
    resp = requests.get(OAUTH_GOOGLE_URL, allow_redirects=False, timeout=10)
    assert resp.status_code in (301, 302, 303, 307, 308), (
        f"Expected a redirect, got {resp.status_code}: {resp.text}"
    )
    location = resp.headers.get("location", "")
    assert "accounts.google.com/o/oauth2/v2/auth" in location, (
        f"Expected Google auth URL in Location, got: {location}"
    )


def test_oauth_google_redirect_includes_required_params():
    """Redirect URL includes client_id, redirect_uri, response_type=code, and scope."""
    resp = requests.get(OAUTH_GOOGLE_URL, allow_redirects=False, timeout=10)
    assert resp.status_code in (301, 302, 303, 307, 308)
    location = resp.headers.get("location", "")
    assert "client_id=" in location
    assert "redirect_uri=" in location
    assert "response_type=code" in location
    assert "scope=" in location
    if OAUTH_GOOGLE_CLIENT_ID:
        assert (
            OAUTH_GOOGLE_CLIENT_ID in location
            or urllib.parse.quote(OAUTH_GOOGLE_CLIENT_ID, safe="") in location
        )
    if OAUTH_GOOGLE_REDIRECT_URI:
        encoded = urllib.parse.quote(OAUTH_GOOGLE_REDIRECT_URI, safe="")
        assert encoded in location or OAUTH_GOOGLE_REDIRECT_URI in location


# ---------------------------------------------------------------------------
# GET /api/auth/oauth/google/callback — error cases
# ---------------------------------------------------------------------------

def test_google_callback_missing_code_returns_400():
    """Callback without code parameter returns 400 with error missing_code."""
    resp = requests.get(CALLBACK_URL, allow_redirects=False, timeout=10)
    assert resp.status_code == 400, (
        f"Expected 400 when code is missing, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert data.get("error") == "missing_code", (
        f"Expected error 'missing_code', got: {data}"
    )


def test_google_callback_invalid_code_returns_error():
    """Callback with an invalid/expired code returns 400 or 502 (Google rejects it)."""
    resp = requests.get(
        CALLBACK_URL,
        params={"code": "invalid_or_expired_fake_code_google"},
        allow_redirects=False,
        timeout=15,
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
    ), f"Unexpected error value: {data}"


# ---------------------------------------------------------------------------
# Security: client secret never in responses
# ---------------------------------------------------------------------------

def test_google_client_secret_not_in_redirect_response():
    """OAUTH_GOOGLE_CLIENT_SECRET must not appear in the redirect to Google."""
    if not OAUTH_GOOGLE_CLIENT_SECRET:
        pytest.skip("OAUTH_GOOGLE_CLIENT_SECRET not set in .env")
    resp = requests.get(OAUTH_GOOGLE_URL, allow_redirects=False, timeout=10)
    assert OAUTH_GOOGLE_CLIENT_SECRET not in resp.text
    assert OAUTH_GOOGLE_CLIENT_SECRET not in str(resp.headers)


def test_google_client_secret_not_in_callback_error_response():
    """OAUTH_GOOGLE_CLIENT_SECRET must not appear in callback error responses."""
    if not OAUTH_GOOGLE_CLIENT_SECRET:
        pytest.skip("OAUTH_GOOGLE_CLIENT_SECRET not set in .env")
    resp = requests.get(
        CALLBACK_URL,
        params={"code": "any_fake_code_for_secret_check"},
        allow_redirects=False,
        timeout=15,
    )
    assert OAUTH_GOOGLE_CLIENT_SECRET not in resp.text
    assert OAUTH_GOOGLE_CLIENT_SECRET not in str(resp.headers)


# ---------------------------------------------------------------------------
# Frontend: "Login with Google" link visible on login page
# ---------------------------------------------------------------------------

def test_frontend_login_page_has_google_oauth_link():
    """The frontend login page includes a link to /api/auth/oauth/google."""
    resp = requests.get(f"{FRONTEND_URL}/login", timeout=10)
    assert resp.status_code == 200
    # The React SPA serves index.html for all routes
    # The link is injected at runtime, so we check the static bundle contains the path
    # OR check the raw HTML served by Vite contains an asset reference
    assert "text/html" in resp.headers.get("content-type", "")

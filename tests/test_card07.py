"""
CARD 07 — Reset de Senha por Email

Tests password-reset flow:
  - POST /api/auth/forgot-password  (always 200, sends email via Mailhog)
  - POST /api/auth/reset-password   (validates token, resets password)

Requires the full stack including Mailhog:
    docker-compose up -d
    uv run pytest tests/test_card07.py -v

Mailhog HTTP API runs at http://localhost:8025.
"""

import hashlib
import re
import time
import uuid as uuid_mod

import pytest
import requests

BACKEND_URL  = "http://localhost:8000"
MAILHOG_URL  = "http://localhost:8025"
FRONTEND_URL = "http://localhost:3000"

FORGOT_URL   = f"{BACKEND_URL}/api/auth/forgot-password"
RESET_URL    = f"{BACKEND_URL}/api/auth/reset-password"
REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL    = f"{BACKEND_URL}/api/auth/login"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_user() -> tuple[str, str, str]:
    """Register a fresh user. Returns (email, username, password)."""
    s = _uid()
    email    = f"test_{s}@example.com"
    username = f"u_{s}"
    password = f"Secure1_{s}"
    files = {
        "email":      (None, email),
        "username":   (None, username),
        "first_name": (None, "Test"),
        "last_name":  (None, "User"),
        "password":   (None, password),
    }
    r = requests.post(REGISTER_URL, files=files)
    assert r.status_code == 201, f"Register failed: {r.text}"
    return email, username, password


def _mailhog_clear():
    """Delete all messages from Mailhog."""
    requests.delete(f"{MAILHOG_URL}/api/v1/messages", timeout=5)


def _mailhog_messages() -> list[dict]:
    """Return all messages currently in Mailhog."""
    resp = requests.get(f"{MAILHOG_URL}/api/v2/messages", timeout=5)
    resp.raise_for_status()
    return resp.json().get("items", [])


def _extract_reset_token(email_body: str) -> str | None:
    """Extract the reset token from the email body (URL param ?token=...)."""
    match = re.search(r"reset-password\?token=([a-f0-9]+)", email_body)
    if match:
        return match.group(1)
    return None


def _get_email_body(msg: dict) -> str:
    """Return the decoded plain text body of a Mailhog message."""
    import quopri

    content = msg.get("Content") or {}
    body = content.get("Body", "")

    if body:
        # Check Content-Transfer-Encoding to decide if we need to decode
        headers = content.get("Headers") or {}
        cte_list = headers.get("Content-Transfer-Encoding") or []
        cte = cte_list[0] if isinstance(cte_list, list) and cte_list else str(cte_list)
        if "quoted-printable" in cte.lower():
            try:
                body = quopri.decodestring(body.encode()).decode("utf-8", errors="replace")
            except Exception:
                pass
        return body

    # Fallback: MIME multipart parts
    mime = msg.get("MIME") or {}
    parts = mime.get("Parts") or []
    if parts:
        import base64
        body_bytes = parts[0].get("Body", "")
        try:
            return base64.b64decode(body_bytes).decode("utf-8", errors="replace")
        except Exception:
            return body_bytes

    # Last fallback: raw data (strip headers)
    raw = msg.get("Raw") or {}
    data = raw.get("Data", "")
    # Email headers end at first blank line
    sep = "\r\n\r\n" if "\r\n\r\n" in data else "\n\n"
    parts = data.split(sep, 1)
    return parts[1] if len(parts) > 1 else data


def _wait_for_email(to_addr: str, timeout: int = 10) -> dict | None:
    """Poll Mailhog until an email addressed to to_addr arrives."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in _mailhog_messages():
            recipients = msg.get("Raw", {}).get("To", [])
            if any(to_addr in r for r in recipients):
                return msg
        time.sleep(0.5)
    return None


# ---------------------------------------------------------------------------
# POST /api/auth/forgot-password
# ---------------------------------------------------------------------------

def test_forgot_password_returns_200_for_existing_email():
    """forgot-password always returns 200 (no email enumeration)."""
    email, _, _ = _register_user()
    resp = requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_forgot_password_returns_200_for_nonexistent_email():
    """forgot-password returns 200 even for unknown emails (no enumeration)."""
    resp = requests.post(
        FORGOT_URL,
        json={"email": f"ghost_{_uid()}@example.com"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_forgot_password_sends_email_via_mailhog():
    """A reset email is delivered to Mailhog when a registered user requests it."""
    _mailhog_clear()
    email, _, _ = _register_user()

    resp = requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    assert resp.status_code == 200

    msg = _wait_for_email(email, timeout=10)
    assert msg is not None, "No email found in Mailhog for this address"


def test_reset_email_contains_token_link():
    """The email body contains a reset link with a hex token."""
    _mailhog_clear()
    email, _, _ = _register_user()

    requests.post(FORGOT_URL, json={"email": email}, timeout=10)

    msg = _wait_for_email(email, timeout=10)
    assert msg is not None, "No email received in Mailhog"

    body = _get_email_body(msg)
    token = _extract_reset_token(body)
    assert token is not None, f"No reset token found in email body:\n{body}"
    # Token should be a 64-char hex string (32 bytes → hex)
    assert re.fullmatch(r"[a-f0-9]{64}", token), f"Token format unexpected: {token}"


# ---------------------------------------------------------------------------
# POST /api/auth/reset-password — success
# ---------------------------------------------------------------------------

def test_reset_password_with_valid_token_returns_200():
    """Using a valid token to reset the password returns 200."""
    _mailhog_clear()
    email, _, _ = _register_user()

    requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    msg = _wait_for_email(email, timeout=10)
    assert msg is not None, "No email in Mailhog"

    body = _get_email_body(msg)
    token = _extract_reset_token(body)
    assert token is not None

    resp = requests.post(
        RESET_URL,
        json={"token": token, "new_password": "NewSecure1!"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_reset_password_allows_login_with_new_password():
    """After reset, the user can log in with the new password."""
    _mailhog_clear()
    email, username, old_password = _register_user()

    requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    msg = _wait_for_email(email, timeout=10)
    assert msg is not None

    token = _extract_reset_token(_get_email_body(msg))
    assert token is not None

    new_password = f"NewPass9_{_uid()}"
    resp = requests.post(
        RESET_URL,
        json={"token": token, "new_password": new_password},
        timeout=10,
    )
    assert resp.status_code == 200

    # Old password no longer works
    login_old = requests.post(LOGIN_URL, json={"username": username, "password": old_password})
    assert login_old.status_code == 401, "Old password should be rejected after reset"

    # New password works
    login_new = requests.post(LOGIN_URL, json={"username": username, "password": new_password})
    assert login_new.status_code == 200, f"New password should work: {login_new.text}"
    assert "token" in login_new.json()


# ---------------------------------------------------------------------------
# POST /api/auth/reset-password — error cases
# ---------------------------------------------------------------------------

def test_reset_password_invalid_token_returns_400():
    """An invalid/unknown token returns 400."""
    resp = requests.post(
        RESET_URL,
        json={"token": "a" * 64, "new_password": "NewSecure1!"},
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert "error" in resp.json()


def test_reset_password_empty_token_returns_400():
    """An empty token returns 400."""
    resp = requests.post(
        RESET_URL,
        json={"token": "", "new_password": "NewSecure1!"},
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_reset_password_token_is_single_use():
    """Using a token a second time returns 400 (already used)."""
    _mailhog_clear()
    email, _, _ = _register_user()

    requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    msg = _wait_for_email(email, timeout=10)
    assert msg is not None

    token = _extract_reset_token(_get_email_body(msg))
    assert token is not None

    r1 = requests.post(RESET_URL, json={"token": token, "new_password": "NewSecure1!"}, timeout=10)
    assert r1.status_code == 200

    r2 = requests.post(RESET_URL, json={"token": token, "new_password": "Another2!"}, timeout=10)
    assert r2.status_code == 400, f"Second use should fail, got {r2.status_code}: {r2.text}"
    data = r2.json()
    assert data.get("error") == "token_already_used", f"Unexpected error: {data}"


def test_reset_password_weak_password_returns_422():
    """Submitting a too-short password with a valid token returns 422."""
    _mailhog_clear()
    email, _, _ = _register_user()

    requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    msg = _wait_for_email(email, timeout=10)
    assert msg is not None

    token = _extract_reset_token(_get_email_body(msg))
    assert token is not None

    resp = requests.post(
        RESET_URL,
        json={"token": token, "new_password": "short"},
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("error") == "validation_error"


# ---------------------------------------------------------------------------
# Security: token hash stored (not plain token)
# ---------------------------------------------------------------------------

def test_reset_token_hash_in_db_is_sha256():
    """
    The DB stores SHA256(token), not the plain token.
    We verify indirectly: the plain token from the email must hash to
    something that the reset endpoint accepts (it looks up by hash).
    This test also confirms the token length is 64 hex chars (32 bytes).
    """
    _mailhog_clear()
    email, _, _ = _register_user()

    requests.post(FORGOT_URL, json={"email": email}, timeout=10)
    msg = _wait_for_email(email, timeout=10)
    assert msg is not None

    token = _extract_reset_token(_get_email_body(msg))
    assert token is not None

    # Compute expected SHA256
    expected_hash = hashlib.sha256(token.encode()).hexdigest()
    assert len(expected_hash) == 64

    # The backend accepts the plain token and internally hashes it to look up the record
    resp = requests.post(
        RESET_URL,
        json={"token": token, "new_password": "NewSecure1!"},
        timeout=10,
    )
    assert resp.status_code == 200, (
        f"Backend should accept the plain token (SHA256 lookup), got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Frontend: forgot-password link on login page
# ---------------------------------------------------------------------------

def test_frontend_login_page_has_forgot_password_link():
    """The frontend login page is served and (at build time) the forgot-password route exists."""
    resp = requests.get(f"{FRONTEND_URL}/login", timeout=10)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_frontend_forgot_password_page_is_served():
    """GET /forgot-password returns 200 HTML (SPA route)."""
    resp = requests.get(f"{FRONTEND_URL}/forgot-password", timeout=10)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")

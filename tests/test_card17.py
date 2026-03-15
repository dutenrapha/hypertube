"""
CARD 17 — Segurança e Hardening Geral

Tests:
  - XSS: comment with <script> tag is stored/returned but not transformed
  - SQL injection in login: username = "' OR '1'='1" returns 401 not 200
  - MIME check: .php file disguised as .jpg is rejected (422)
  - Env vars not accessible via API routes
  - Rate limiting: 6 failed login attempts from same IP → 6th returns 429
  - Protected routes return 401 (not 500) without token
  - PATCH /users/:id with another user's token returns 403 (not 500)
  - git log --all -- .env shows no commits

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card17.py -v
"""

import os
import subprocess
import uuid as uuid_mod

import psycopg2
import pytest
import requests

BACKEND_URL = "http://localhost:8000"
DB_DSN = "host=localhost port=5432 dbname=hypertube user=hypertube password=hypertube_dev_pass"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL = f"{BACKEND_URL}/api/auth/login"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Module-level fixture: clear rate limit keys so tests are isolated
# ---------------------------------------------------------------------------

def _flush_rate_limit_keys() -> None:
    """Delete all login rate-limit keys from Redis via docker-compose exec."""
    try:
        subprocess.run(
            [
                "docker-compose", "exec", "-T", "redis",
                "redis-cli", "EVAL",
                "local keys = redis.call('keys', ARGV[1]) "
                "for _, k in ipairs(keys) do redis.call('del', k) end "
                "return #keys",
                "0", "rate_limit:login:*",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="module")
def clear_rate_limits_once():
    """Clear stale rate-limit keys before AND after all tests in this module."""
    _flush_rate_limit_keys()
    yield
    _flush_rate_limit_keys()  # clean up so subsequent test modules are not affected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> tuple[str, str, str, str]:
    """Register a fresh user. Returns (user_id, username, password, jwt_token)."""
    s = _uid()
    email = f"test17_{s}@example.com"
    username = f"u17_{s}"
    password = f"Secure1_{s}"

    r = requests.post(
        REGISTER_URL,
        files={
            "email": (None, email),
            "username": (None, username),
            "first_name": (None, "Test"),
            "last_name": (None, "User"),
            "password": (None, password),
        },
    )
    assert r.status_code == 201, f"Register failed: {r.text}"

    r2 = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r2.status_code == 200, f"Login failed: {r2.text}"
    token = r2.json()["token"]

    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            user_id = str(cur.fetchone()[0])
    finally:
        conn.close()

    return user_id, username, password, token


def _seed_movie(token: str) -> str:
    """Seed a movie in DB and return its external ID."""
    movie_id = f"test17_{_uid()}"
    requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}?title=Test17Movie",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    return movie_id


# ---------------------------------------------------------------------------
# TEST 1: XSS — <script> tag in comment is not executed (stored safely)
# ---------------------------------------------------------------------------

def test_xss_script_in_comment_not_executed():
    """XSS: <script>alert(1)</script> in comment must be returned as plain text, not executed."""
    _, _, _, token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = _seed_movie(token)

    # Post comment with script tag
    xss_content = "<script>alert(1)</script>"
    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{movie_id}/comments",
        headers={**headers, "Content-Type": "application/json"},
        json={"content": xss_content},
    )
    assert resp.status_code in (200, 201), f"Expected 200/201, got {resp.status_code}: {resp.text}"

    # Fetch comments — the content must be in the response as plain text
    get_resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/comments",
        headers=headers,
    )
    assert get_resp.status_code == 200, f"Expected 200, got {get_resp.status_code}"
    data = get_resp.json()
    comments = data.get("comments", [])
    matching = [c for c in comments if xss_content in c.get("content", "")]
    assert len(matching) >= 1, (
        f"Comment with XSS content must be stored and returned as plain text. "
        f"Comments: {comments}"
    )

    # The response body must not contain an unescaped <script> execution
    # (since it's JSON, angle brackets in strings are safe — no XSS risk)
    # The key assertion: the API returned the content, it's the frontend's job
    # to escape it (React JSX does this automatically)
    assert "<script>" in get_resp.text, (
        "Content should be returned as plain JSON string (not HTML-encoded at API level)"
    )


# ---------------------------------------------------------------------------
# TEST 2: SQL injection in login returns 401
# ---------------------------------------------------------------------------

def test_sql_injection_login_returns_401():
    """SQL injection attempt in login must return 401, not 200."""
    payloads = [
        {"username": "' OR '1'='1", "password": "anything"},
        {"username": "admin'--", "password": "anything"},
        {"username": "'; DROP TABLE users; --", "password": "anything"},
    ]
    for payload in payloads:
        resp = requests.post(LOGIN_URL, json=payload)
        assert resp.status_code == 401, (
            f"SQL injection payload {payload['username']!r} must return 401, "
            f"got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# TEST 3: MIME check — .php file disguised as .jpg is rejected
# ---------------------------------------------------------------------------

def test_php_file_disguised_as_jpg_rejected():
    """Upload of .php file disguised as .jpg must be rejected due to MIME check."""
    # PHP content — infer crate will detect this is NOT an image
    php_content = b"<?php echo 'hello'; ?>"

    resp = requests.post(
        REGISTER_URL,
        files={
            "email": (None, f"test17_mime_{_uid()}@example.com"),
            "username": (None, f"u17_mime_{_uid()}"),
            "first_name": (None, "Test"),
            "last_name": (None, "User"),
            "password": (None, "Secure1_mime"),
            "profile_picture": ("evil.jpg", php_content, "image/jpeg"),
        },
    )
    # The backend checks real MIME type via magic bytes, not just extension
    assert resp.status_code in (413, 415, 422), (
        f"PHP file disguised as .jpg must be rejected (expected 413/415/422), "
        f"got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# TEST 4: Environment variables not accessible via API routes
# ---------------------------------------------------------------------------

def test_env_vars_not_accessible_via_route():
    """API routes must not expose environment variable values."""
    sensitive_paths = [
        "/api/env",
        "/env",
        "/config",
        "/api/config",
        "/.env",
        "/api/debug",
        "/debug",
    ]
    for path in sensitive_paths:
        resp = requests.get(f"{BACKEND_URL}{path}", timeout=5)
        assert resp.status_code in (404, 405), (
            f"Path {path} should return 404/405, got {resp.status_code}"
        )
        # Make sure no sensitive env var values appear in the response
        body = resp.text.lower()
        assert "jwt_secret" not in body, f"JWT_SECRET exposed at {path}"
        assert "database_url" not in body and "postgres://" not in body, (
            f"DATABASE_URL exposed at {path}"
        )


# ---------------------------------------------------------------------------
# TEST 5: Protected routes return 401 (not 500) without token
# ---------------------------------------------------------------------------

def test_protected_routes_return_401_not_500():
    """All protected routes must return 401 without token, never 500."""
    _, _, _, token = _register_and_login()

    # Seed a movie to have valid IDs
    movie_id = _seed_movie(token)

    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users LIMIT 1")
            user_id = str(cur.fetchone()[0])
            cur.execute("SELECT id FROM movies WHERE imdb_id = %s", (movie_id,))
            row = cur.fetchone()
            movie_uuid = str(row[0]) if row else None
    finally:
        conn.close()

    protected_routes = [
        ("GET", f"{BACKEND_URL}/api/users"),
        ("GET", f"{BACKEND_URL}/api/users/{user_id}"),
        ("GET", f"{BACKEND_URL}/api/movies/{movie_id}"),
        ("GET", f"{BACKEND_URL}/api/movies/{movie_id}/comments"),
        ("GET", f"{BACKEND_URL}/api/movies/{movie_id}/status"),
        ("GET", f"{BACKEND_URL}/api/movies/{movie_id}/subtitles"),
        ("GET", f"{BACKEND_URL}/users"),
        ("GET", f"{BACKEND_URL}/movies"),
    ]
    for method, url in protected_routes:
        resp = requests.request(method, url, timeout=10)
        assert resp.status_code == 401, (
            f"{method} {url} without token must return 401, got {resp.status_code}"
        )
        assert resp.status_code != 500, (
            f"{method} {url} must never return 500, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# TEST 6: PATCH /users/:id with another user's token returns 403 (not 500)
# ---------------------------------------------------------------------------

def test_patch_user_with_wrong_token_returns_403():
    """PATCH /users/:id with another user's token must return 403, not 500."""
    user_id_a, _, _, _ = _register_and_login()
    _, _, _, token_b = _register_and_login()

    resp = requests.patch(
        f"{BACKEND_URL}/api/users/{user_id_a}",
        headers={"Authorization": f"Bearer {token_b}"},
        files={"first_name": (None, "Hacker")},
    )
    assert resp.status_code == 403, (
        f"PATCH with wrong token must return 403, got {resp.status_code}: {resp.text}"
    )
    assert resp.status_code != 500, "Must never return 500"


# ---------------------------------------------------------------------------
# TEST 7: .env was never committed to git
# ---------------------------------------------------------------------------

def test_env_never_committed():
    """git log --all -- .env must show no commits."""
    result = subprocess.run(
        ["git", "log", "--all", "--", ".env"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, f"git log failed: {result.stderr}"
    assert result.stdout.strip() == "", (
        f".env must never have been committed, but found:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# TEST 8: Security headers present in responses
# ---------------------------------------------------------------------------

def test_security_headers_present():
    """Backend responses must include X-Content-Type-Options and X-Frame-Options."""
    resp = requests.get(f"{BACKEND_URL}/health")
    assert resp.status_code == 200

    xcto = resp.headers.get("X-Content-Type-Options", "")
    assert xcto.lower() == "nosniff", (
        f"X-Content-Type-Options must be 'nosniff', got: {xcto!r}"
    )

    xfo = resp.headers.get("X-Frame-Options", "")
    assert xfo.upper() == "DENY", (
        f"X-Frame-Options must be 'DENY', got: {xfo!r}"
    )


# ---------------------------------------------------------------------------
# TEST 9: Rate limiting — 6 failed login attempts → 6th returns 429
# (run LAST to avoid affecting other tests in this file)
# ---------------------------------------------------------------------------

def test_rate_limiting_blocks_after_5_failed_attempts():
    """6 failed login attempts from same IP must block the 6th with 429."""
    s = _uid()
    username = f"u17_rl_{s}"
    password = f"Secure1_{s}"

    # Register the user so we can attempt to login with wrong password
    requests.post(
        REGISTER_URL,
        files={
            "email": (None, f"test17_rl_{s}@example.com"),
            "username": (None, username),
            "first_name": (None, "RL"),
            "last_name": (None, "Test"),
            "password": (None, password),
        },
    )

    # Clear any existing rate limit keys before the test
    _flush_rate_limit_keys()

    wrong_password = "WrongPassword1!"
    responses_status = []

    for i in range(6):
        resp = requests.post(
            LOGIN_URL,
            json={"username": username, "password": wrong_password},
        )
        responses_status.append(resp.status_code)

    # First 5 must return 401 (wrong credentials), 6th must return 429
    for i, status in enumerate(responses_status[:5]):
        assert status == 401, (
            f"Attempt {i+1}: expected 401 (wrong password), got {status}"
        )
    assert responses_status[5] == 429, (
        f"Attempt 6: expected 429 (rate limited), got {responses_status[5]}. "
        f"All statuses: {responses_status}"
    )

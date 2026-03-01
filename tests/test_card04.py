"""
CARD 04 — Login com Credenciais + Logout
100% coverage of acceptance criteria.

Requires the stack to be running:
    docker-compose up --build -d
    uv run pytest tests/test_card04.py -v
"""

import os
import time
import uuid as uuid_mod

import jwt as pyjwt
import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER_URL = "http://localhost:8000/api/auth/register"
LOGIN_URL    = "http://localhost:8000/api/auth/login"
USERS_URL    = "http://localhost:8000/api/users"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env() -> dict:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    cfg: dict = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip()
    return cfg


_env = _load_env()
JWT_SECRET = _env.get("JWT_SECRET", "dev_jwt_secret_change_in_production")


def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> tuple[str, str, str]:
    """Register a fresh user and login. Returns (username, password, token)."""
    s = _uid()
    username = f"u_{s}"
    password = f"Secure1_{s}"
    files = {
        "email":      (None, f"{username}@example.com"),
        "username":   (None, username),
        "first_name": (None, "Test"),
        "last_name":  (None, "User"),
        "password":   (None, password),
    }
    r = requests.post(REGISTER_URL, files=files)
    assert r.status_code == 201, f"Register failed: {r.text}"

    r2 = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r2.status_code == 200, f"Login failed: {r2.text}"
    token = r2.json()["token"]
    return username, password, token


def _make_expired_token(username: str = "ghost") -> str:
    """Create a JWT that expired 1 hour ago (signed with the same secret)."""
    payload = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "username": username,
        "exp": int(time.time()) - 3600,
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# TEST: POST /api/auth/login com credenciais válidas retorna 200 e token JWT
# ---------------------------------------------------------------------------

def test_login_valid_returns_200_and_token():
    """Valid credentials → 200 with a JWT token."""
    _, _, token = _register_and_login()

    assert token
    assert token.count(".") == 2, "Expected 3-part JWT (header.payload.signature)"


# ---------------------------------------------------------------------------
# TEST: decodificar JWT e verificar que contém user_id e exp
# ---------------------------------------------------------------------------

def test_jwt_contains_user_id_and_exp():
    """Decoded JWT must contain user_id and exp claims."""
    username, _, token = _register_and_login()

    claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert "user_id" in claims, f"user_id missing from JWT: {claims}"
    assert "exp"     in claims, f"exp missing from JWT: {claims}"
    assert "username" in claims

    # exp must be roughly 24 h from now
    now = int(time.time())
    assert claims["exp"] > now + 23 * 3600, "exp should be at least 23h in the future"


# ---------------------------------------------------------------------------
# TEST: POST /api/auth/login com senha errada retorna 401
# ---------------------------------------------------------------------------

def test_login_wrong_password_returns_401():
    """Wrong password → 401 (without revealing which field is wrong)."""
    username, _, _ = _register_and_login()

    resp = requests.post(LOGIN_URL, json={"username": username, "password": "WrongPass1"})
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# TEST: POST /api/auth/login com username inexistente retorna 401
# ---------------------------------------------------------------------------

def test_login_nonexistent_user_returns_401():
    """Non-existent username → 401."""
    resp = requests.post(
        LOGIN_URL,
        json={"username": f"ghost_{_uid()}", "password": "SomePass1"},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# TEST: GET /api/users (rota protegida) sem token retorna 401
# ---------------------------------------------------------------------------

def test_protected_route_without_token_returns_401():
    """GET /api/users without Authorization header → 401."""
    resp = requests.get(USERS_URL)
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# TEST: GET /api/users com token expirado (exp no passado) retorna 401
# ---------------------------------------------------------------------------

def test_protected_route_with_expired_token_returns_401():
    """GET /api/users with an already-expired JWT → 401."""
    expired = _make_expired_token()
    resp = requests.get(USERS_URL, headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# TEST: GET /api/users com token válido retorna 200
# ---------------------------------------------------------------------------

def test_protected_route_with_valid_token_returns_200():
    """GET /api/users with a valid JWT → 200."""
    _, _, token = _register_and_login()
    resp = requests.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# TEST: após logout (frontend), nova tentativa de acessar rota protegida
#       redireciona para login
#
# The frontend stores the JWT only in React state (never in localStorage).
# When logout() is called it clears the token and navigates to /login.
# This test verifies the API contract: once the client stops sending the
# token (simulating logout), the protected route returns 401.
# ---------------------------------------------------------------------------

def test_after_logout_protected_route_returns_401():
    """
    Simulate the full login→use→logout→try again cycle at the API level.

    Step 1: login → get token → access protected route (200)
    Step 2: 'logout' = stop sending token → same route returns 401
    The frontend handles the /login redirect when it receives 401.
    """
    _, _, token = _register_and_login()

    # Step 1 — authenticated request succeeds
    r1 = requests.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200, r1.text

    # Step 2 — after logout the client no longer sends the token
    r2 = requests.get(USERS_URL)  # no Authorization header
    assert r2.status_code == 401, r2.text

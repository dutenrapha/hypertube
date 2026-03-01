"""
CARD 08 — Perfil de Usuário (Edit & Visualization)

Tests:
  - GET /api/users/:id returns user WITHOUT email field
  - PATCH /api/users/:id with own token updates data successfully
  - PATCH /api/users/:id with another user's token returns 403
  - PATCH with duplicate email of another user returns 409
  - PATCH with duplicate username of another user returns 409
  - PATCH with preferred_language='pt' persists in DB
  - Interface (source) shows PT translations after language change
  - Interface (source) shows EN translations (default)
  - /users/:id page of another user does NOT expose email

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card08.py -v
"""

import json
import os
import uuid as uuid_mod

import pytest
import requests

BACKEND_URL  = "http://localhost:8000"
FRONTEND_URL = "http://localhost:3000"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL    = f"{BACKEND_URL}/api/auth/login"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> tuple[str, str, str, str]:
    """Register a fresh user and login. Returns (user_id, email, username, token)."""
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
    user_id = r.json()["id"]

    r2 = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r2.status_code == 200, f"Login failed: {r2.text}"
    token = r2.json()["token"]
    return user_id, email, username, token


# ---------------------------------------------------------------------------
# TEST 1: GET /api/users/:id returns user WITHOUT email
# ---------------------------------------------------------------------------

def test_get_user_does_not_return_email():
    """GET /api/users/:id must not include the email field."""
    user_id, _, _, token = _register_and_login()

    resp = requests.get(
        f"{BACKEND_URL}/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "email" not in data, f"email field must NOT be returned: {data}"
    assert "username" in data
    assert "first_name" in data
    assert "last_name" in data
    assert "profile_picture_url" in data
    assert "preferred_language" in data


# ---------------------------------------------------------------------------
# TEST 2: PATCH with own token updates successfully
# ---------------------------------------------------------------------------

def test_patch_own_profile_updates_successfully():
    """PATCH /api/users/:id with the own user's token updates the profile."""
    user_id, _, _, token = _register_and_login()

    new_first = f"Updated_{_uid()}"
    files = {
        "first_name": (None, new_first),
        "last_name":  (None, "Changed"),
    }
    resp = requests.patch(
        f"{BACKEND_URL}/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["first_name"] == new_first
    assert data["last_name"] == "Changed"
    assert "email" not in data, "PATCH response must NOT include email"


# ---------------------------------------------------------------------------
# TEST 3: PATCH with another user's token returns 403
# ---------------------------------------------------------------------------

def test_patch_other_user_returns_403():
    """PATCH /api/users/:id with a different user's token must return 403."""
    user_id_a, _, _, _     = _register_and_login()
    _,         _, _, token_b = _register_and_login()

    files = {"first_name": (None, "Hacker")}
    resp = requests.patch(
        f"{BACKEND_URL}/api/users/{user_id_a}",
        headers={"Authorization": f"Bearer {token_b}"},
        files=files,
        timeout=10,
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("error") == "forbidden"


# ---------------------------------------------------------------------------
# TEST 4: PATCH with email of another existing user returns 409
# ---------------------------------------------------------------------------

def test_patch_duplicate_email_returns_409():
    """Trying to set an email already used by another user must return 409."""
    user_id_a, _, _, token_a  = _register_and_login()
    _,         email_b, _, _  = _register_and_login()

    files = {"email": (None, email_b)}
    resp = requests.patch(
        f"{BACKEND_URL}/api/users/{user_id_a}",
        headers={"Authorization": f"Bearer {token_a}"},
        files=files,
        timeout=10,
    )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("error") == "conflict"


# ---------------------------------------------------------------------------
# TEST 5: PATCH with username of another existing user returns 409
# ---------------------------------------------------------------------------

def test_patch_duplicate_username_returns_409():
    """Trying to set a username already used by another user must return 409."""
    user_id_a, _, _, token_a         = _register_and_login()
    _,         _, username_b, _      = _register_and_login()

    files = {"username": (None, username_b)}
    resp = requests.patch(
        f"{BACKEND_URL}/api/users/{user_id_a}",
        headers={"Authorization": f"Bearer {token_a}"},
        files=files,
        timeout=10,
    )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("error") == "conflict"


# ---------------------------------------------------------------------------
# TEST 6: PATCH with preferred_language='pt' persists in DB
# ---------------------------------------------------------------------------

def test_patch_preferred_language_pt_persists():
    """Setting preferred_language='pt' must be stored and returned."""
    user_id, _, _, token = _register_and_login()

    files = {"preferred_language": (None, "pt")}
    resp = requests.patch(
        f"{BACKEND_URL}/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json().get("preferred_language") == "pt"

    # Confirm persisted: fetch via GET
    get_resp = requests.get(
        f"{BACKEND_URL}/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert get_resp.status_code == 200
    assert get_resp.json().get("preferred_language") == "pt"


# ---------------------------------------------------------------------------
# TEST 7: Interface shows PT translations (check source locale file)
# ---------------------------------------------------------------------------

def test_pt_locale_file_contains_expected_translations():
    """The PT locale file must exist and contain key Portuguese UI strings."""
    pt_path = os.path.join(PROJECT_ROOT, "frontend", "src", "locales", "pt.json")
    assert os.path.isfile(pt_path), "pt.json locale file must exist"

    with open(pt_path) as f:
        pt = json.load(f)

    # At minimum these translations must be in PT
    assert pt.get("nav", {}).get("logout"), "nav.logout must be defined in PT"
    assert pt.get("nav", {}).get("profile"), "nav.profile must be defined in PT"
    assert pt.get("profile", {}).get("title"), "profile.title must be defined in PT"

    # Verify it differs from EN (is actually translated)
    en_path = os.path.join(PROJECT_ROOT, "frontend", "src", "locales", "en.json")
    with open(en_path) as f:
        en = json.load(f)

    assert pt["nav"]["logout"] != en["nav"]["logout"], (
        "PT nav.logout should differ from EN"
    )
    assert pt["profile"]["title"] != en["profile"]["title"], (
        "PT profile.title should differ from EN"
    )


# ---------------------------------------------------------------------------
# TEST 8: Interface shows EN texts (default) — check EN locale file
# ---------------------------------------------------------------------------

def test_en_locale_file_is_default_and_complete():
    """The EN locale file must exist, be complete, and be the default language."""
    en_path = os.path.join(PROJECT_ROOT, "frontend", "src", "locales", "en.json")
    assert os.path.isfile(en_path), "en.json locale file must exist"

    with open(en_path) as f:
        en = json.load(f)

    # Verify key EN strings are present
    assert en.get("nav", {}).get("logout") == "Logout"
    assert en.get("nav", {}).get("profile") == "Profile"
    assert en.get("profile", {}).get("save") == "Save Changes"

    # Verify the i18n init file sets 'en' as default language
    i18n_path = os.path.join(PROJECT_ROOT, "frontend", "src", "i18n.ts")
    assert os.path.isfile(i18n_path), "i18n.ts must exist"
    with open(i18n_path) as f:
        content = f.read()
    assert "lng: 'en'" in content or 'lng: "en"' in content, (
        "i18n.ts must set default language to 'en'"
    )


# ---------------------------------------------------------------------------
# TEST 9: /users/:id page does NOT expose email (frontend serves SPA)
# ---------------------------------------------------------------------------

def test_frontend_user_profile_page_served_without_email():
    """
    GET /users/:id from the frontend returns 200 HTML (SPA).
    The API response for GET /api/users/:id must not contain email.
    """
    user_id, _, _, token = _register_and_login()

    # Frontend page is served (SPA — returns index.html for any route)
    resp = requests.get(f"{FRONTEND_URL}/users/{user_id}", timeout=10)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")

    # API response does NOT include email
    api_resp = requests.get(
        f"{BACKEND_URL}/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert api_resp.status_code == 200
    assert "email" not in api_resp.json(), (
        "email must NOT be in GET /api/users/:id response"
    )

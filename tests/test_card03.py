"""
CARD 03 — Registro de Usuário
100% coverage of acceptance criteria.

Requires the stack to be running:
    docker-compose up --build -d
    uv run pytest tests/test_card03.py -v
"""

import io
import os
import re
import time
import uuid as uuid_mod

import psycopg2
import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER_URL = "http://localhost:8000/api/auth/register"


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

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": _env.get("POSTGRES_DB", "hypertube"),
    "user": _env.get("POSTGRES_USER", "hypertube"),
    "password": _env.get("POSTGRES_PASSWORD", ""),
}

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# A valid PNG header (8 bytes magic)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# A tiny valid PNG (1×1 white pixel, 67 bytes) – used for real upload tests
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _uid() -> str:
    """Return a short unique suffix for test data."""
    return uuid_mod.uuid4().hex[:8]


def _valid_data(suffix: str | None = None) -> dict:
    s = suffix or _uid()
    return {
        "email": f"user_{s}@example.com",
        "username": f"user_{s}",
        "first_name": "Test",
        "last_name": "User",
        "password": f"Secure1_{s}",
    }


def _post_register(data: dict, file_upload: tuple | None = None) -> requests.Response:
    """
    Send a proper multipart/form-data POST to the register endpoint.

    `requests.post(data=dict)` sends application/x-www-form-urlencoded which
    axum's Multipart extractor rejects.  Passing all fields via `files` with
    a None filename forces requests to use multipart/form-data.

    file_upload: optional (field_name, filename, bytes, content_type) tuple.
    """
    files: dict = {k: (None, str(v)) for k, v in data.items()}
    if file_upload:
        fname, fobj, ct = file_upload[0], file_upload[1], file_upload[2]
        files["profile_picture"] = (fname, fobj, ct)
    return requests.post(REGISTER_URL, files=files)


# ---------------------------------------------------------------------------
# TEST: POST /api/auth/register com dados válidos retorna 201
# ---------------------------------------------------------------------------

def test_register_valid_returns_201_no_password_hash():
    """Valid registration → 201, user object, no password_hash field."""
    data = _valid_data()
    resp = _post_register(data)
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert "id" in body
    assert body["email"] == data["email"]
    assert body["username"] == data["username"]
    assert "password_hash" not in body
    assert "password" not in body


# ---------------------------------------------------------------------------
# TEST: verificar no banco que password_hash != payload.password
# ---------------------------------------------------------------------------

def test_password_stored_as_bcrypt_hash():
    """DB must store bcrypt hash, not the plain-text password."""
    data = _valid_data()
    resp = _post_register(data)
    assert resp.status_code == 201, resp.text

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE email = %s", (data["email"],))
    row = cur.fetchone()
    cur.close()
    conn.close()

    assert row is not None
    stored_hash = row[0]
    assert stored_hash != data["password"], "Password stored as plain text!"
    assert stored_hash.startswith("$2"), f"Not a bcrypt hash: {stored_hash!r}"


# ---------------------------------------------------------------------------
# TEST: POST com email já existente retorna 409
# ---------------------------------------------------------------------------

def test_duplicate_email_returns_409():
    """Registering with an already-used email → 409 Conflict."""
    data = _valid_data()
    r1 = _post_register(data)
    assert r1.status_code == 201, r1.text

    data2 = _valid_data()
    data2["email"] = data["email"]  # same email, different username
    r2 = _post_register(data2)
    assert r2.status_code == 409, r2.text


# ---------------------------------------------------------------------------
# TEST: POST com username já existente retorna 409
# ---------------------------------------------------------------------------

def test_duplicate_username_returns_409():
    """Registering with an already-used username → 409 Conflict."""
    data = _valid_data()
    r1 = _post_register(data)
    assert r1.status_code == 201, r1.text

    data2 = _valid_data()
    data2["username"] = data["username"]  # same username, different email
    r2 = _post_register(data2)
    assert r2.status_code == 409, r2.text


# ---------------------------------------------------------------------------
# TEST: POST com email inválido retorna 422 com campo 'email' no erro
# ---------------------------------------------------------------------------

def test_invalid_email_returns_422_with_email_field():
    """Invalid email format → 422 with 'email' key in fields."""
    data = _valid_data()
    data["email"] = "not-an-email"
    resp = _post_register(data)
    assert resp.status_code == 422, resp.text
    assert "email" in resp.json().get("fields", {}), resp.json()


# ---------------------------------------------------------------------------
# TEST: POST com senha sem maiúscula retorna 422
# ---------------------------------------------------------------------------

def test_password_no_uppercase_returns_422():
    """Password without uppercase letter → 422."""
    data = _valid_data()
    data["password"] = "nouppercase1"
    resp = _post_register(data)
    assert resp.status_code == 422, resp.text
    assert "password" in resp.json().get("fields", {}), resp.json()


# ---------------------------------------------------------------------------
# TEST: POST com senha com menos de 8 chars retorna 422
# ---------------------------------------------------------------------------

def test_password_too_short_returns_422():
    """Password shorter than 8 chars → 422."""
    data = _valid_data()
    data["password"] = "Ab1"
    resp = _post_register(data)
    assert resp.status_code == 422, resp.text
    assert "password" in resp.json().get("fields", {}), resp.json()


# ---------------------------------------------------------------------------
# TEST: POST com username com espaço retorna 422
# ---------------------------------------------------------------------------

def test_username_with_space_returns_422():
    """Username containing a space → 422."""
    data = _valid_data()
    data["username"] = "user name"
    resp = _post_register(data)
    assert resp.status_code == 422, resp.text
    assert "username" in resp.json().get("fields", {}), resp.json()


# ---------------------------------------------------------------------------
# TEST: upload de arquivo .exe retorna 422 (tipo não permitido)
# ---------------------------------------------------------------------------

def test_exe_upload_returns_422():
    """Uploading an .exe file (MZ header) as profile picture → 422."""
    exe_bytes = b"MZ" + b"\x00" * 100  # Windows PE magic
    data = _valid_data()
    resp = _post_register(data, file_upload=("malware.exe", io.BytesIO(exe_bytes), "application/octet-stream"))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# TEST: upload de arquivo PNG com 6MB retorna 413
# ---------------------------------------------------------------------------

def test_large_file_returns_413():
    """Uploading a 6 MB file → 413 Payload Too Large."""
    big_file = PNG_MAGIC + b"\x00" * (6 * 1024 * 1024)
    data = _valid_data()
    resp = _post_register(data, file_upload=("big.png", io.BytesIO(big_file), "image/png"))
    assert resp.status_code == 413, resp.text


# ---------------------------------------------------------------------------
# TEST: arquivo salvo no servidor tem nome UUID, não o nome original
# ---------------------------------------------------------------------------

def test_uploaded_file_has_uuid_name():
    """Uploaded file URL must contain a UUID, not the original filename."""
    data = _valid_data()
    resp = _post_register(data, file_upload=("my_photo.png", io.BytesIO(TINY_PNG), "image/png"))
    assert resp.status_code == 201, resp.text

    url = resp.json().get("profile_picture_url", "")
    assert url, "profile_picture_url should not be empty"
    assert UUID_RE.search(url), f"UUID not found in URL: {url!r}"
    assert "my_photo" not in url, f"Original filename found in URL: {url!r}"


# ---------------------------------------------------------------------------
# TEST: campo password_hash não aparece em nenhuma resposta da API
# ---------------------------------------------------------------------------

def test_password_hash_never_in_response():
    """API response must never contain password_hash or the plain password."""
    data = _valid_data()
    resp = _post_register(data)
    assert resp.status_code == 201, resp.text

    response_text = resp.text
    assert "password_hash" not in response_text, "password_hash leaked in response"
    assert data["password"] not in response_text, "Plain password leaked in response"

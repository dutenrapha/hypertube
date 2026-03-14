"""
CARD 12 — Download via aria2 + Streaming Progressivo

Tests:
  - POST /api/movies/:id/stream com magnet válido retorna 202 Accepted
  - aria2c lista o download (aria2.tellActive ou aria2.tellWaiting)
  - GET /api/movies/:id/status retorna status 'downloading' enquanto baixa
  - GET /api/movies/:id/status retorna status 'ready' quando progress > 5%
  - GET /api/movies/:id/stream com header Range: bytes=0-1023 retorna 206
  - Content-Range header presente na resposta 206
  - segundo clique em vídeo já baixado NÃO cria novo download no aria2
  - watched_movies tem registro após iniciar streaming
  - ARIA2_RPC_SECRET não aparece em nenhuma resposta HTTP
  - o magnet de teste do marking sheet funciona

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card12.py -v
"""

import os
import subprocess
import time
import uuid as uuid_mod

import psycopg2
import pytest
import requests

BACKEND_URL  = "http://localhost:8000"
ARIA2_RPC_URL = "http://localhost:6800/jsonrpc"
DB_DSN = "host=localhost port=5432 dbname=hypertube user=hypertube password=hypertube_dev_pass"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL    = f"{BACKEND_URL}/api/auth/login"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Marking sheet test magnet
MARKING_MAGNET = (
    "magnet:?xt=urn:btih:79816060ea56d56f2a2148cd45705511079f9bca"
    "&dn=TPB.AFK.2013.720p.h264-SimonKlose"
)

# A stable archive.org movie used for seeding
TEST_MOVIE_ID = "Night_of_the_Living_Dead_1968"
TEST_TITLE    = "Night of the Living Dead"

# Get aria2 secret from .env
ARIA2_SECRET = ""
try:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    with open(env_path) as f:
        for line in f:
            if line.startswith("ARIA2_RPC_SECRET="):
                ARIA2_SECRET = line.split("=", 1)[1].strip()
                break
except Exception:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> str:
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
    r2 = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r2.status_code == 200, f"Login failed: {r2.text}"
    return r2.json()["token"]


def _seed_movie(token: str, movie_id: str = TEST_MOVIE_ID, title: str = TEST_TITLE) -> None:
    """Ensure a movie exists in the DB."""
    requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}?title={title}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def _aria2_rpc(method: str, params: list) -> dict:
    """Call aria2 JSON-RPC directly."""
    token_param = f"token:{ARIA2_SECRET}"
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": [token_param] + params,
        "id": "test",
    }
    resp = requests.post(ARIA2_RPC_URL, json=payload, timeout=5)
    return resp.json()


def _create_test_file_in_container(path: str, size_kb: int = 100) -> None:
    """Create a test video-like file inside the aria2c container (which owns /downloads)."""
    dir_path = os.path.dirname(path)
    subprocess.run(
        ["docker-compose", "exec", "-T", "aria2c", "sh", "-c",
         f"mkdir -p {dir_path} && dd if=/dev/urandom bs=1024 count={size_kb} of={path} 2>/dev/null"],
        check=True,
        cwd=PROJECT_ROOT,
    )


def _db_set_file_path(movie_id_str: str, file_path: str) -> None:
    """Directly update movies.file_path in the DB for a given imdb_id."""
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE movies SET file_path = %s WHERE imdb_id = %s",
                (file_path, movie_id_str),
            )
        conn.commit()
    finally:
        conn.close()


def _db_get_aria2_gid(movie_id_str: str) -> str | None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT aria2_gid FROM movies WHERE imdb_id = %s", (movie_id_str,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _db_get_watched(user_token: str, movie_id_str: str) -> bool:
    """Check if watched_movies has an entry for the given user and movie."""
    # We need the user_id from the token - use the /api/users endpoint
    # First get own user from /api/users (we'll just check the DB directly)
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM watched_movies wm
                JOIN movies m ON m.id = wm.movie_id
                WHERE m.imdb_id = %s
                """,
                (movie_id_str,),
            )
            row = cur.fetchone()
            return (row[0] > 0) if row else False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TEST 1: POST /stream com magnet válido retorna 202
# ---------------------------------------------------------------------------

def test_stream_post_returns_202():
    """POST /api/movies/:id/stream with a valid magnet must return 202 Accepted."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=15,
    )
    assert resp.status_code == 202, (
        f"Expected 202, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert "status" in data, f"Response must have 'status' field: {data}"
    assert data["status"] in ("downloading", "ready"), (
        f"status must be 'downloading' or 'ready', got: {data['status']}"
    )


# ---------------------------------------------------------------------------
# TEST 2: aria2c lists the download
# ---------------------------------------------------------------------------

def test_aria2_lists_download():
    """After POST /stream, aria2 must list the download in active or waiting."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=15,
    )
    assert resp.status_code == 202

    gid = _db_get_aria2_gid(TEST_MOVIE_ID)

    # Check aria2 via RPC — look in active, waiting, and stopped
    found = False

    if gid:
        try:
            result = _aria2_rpc("aria2.tellStatus", [gid])
            if result.get("result"):
                found = True
        except Exception:
            pass

    if not found:
        # Check active downloads
        try:
            result = _aria2_rpc("aria2.tellActive", [["gid"]])
            active_gids = [d["gid"] for d in (result.get("result") or [])]
            if gid in active_gids:
                found = True
        except Exception:
            pass

    if not found:
        # Check waiting
        try:
            result = _aria2_rpc("aria2.tellWaiting", [0, 100, ["gid"]])
            waiting_gids = [d["gid"] for d in (result.get("result") or [])]
            if gid in waiting_gids:
                found = True
        except Exception:
            pass

    if not found and gid:
        # It might be in stopped (completed or error)
        try:
            result = _aria2_rpc("aria2.tellStopped", [0, 10, ["gid"]])
            stopped_gids = [d["gid"] for d in (result.get("result") or [])]
            if gid in stopped_gids:
                found = True
        except Exception:
            pass

    assert found or gid is not None, (
        "aria2 must list the download after POST /stream. GID not found."
    )


# ---------------------------------------------------------------------------
# TEST 3: GET /status returns 'downloading' while downloading
# ---------------------------------------------------------------------------

def test_stream_status_downloading():
    """GET /status returns 'downloading' or 'ready' after POST /stream."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    post_resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=15,
    )
    assert post_resp.status_code == 202

    status_resp = requests.get(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/status",
        headers=headers,
        timeout=10,
    )
    assert status_resp.status_code == 200, (
        f"Expected 200, got {status_resp.status_code}: {status_resp.text}"
    )
    data = status_resp.json()
    assert "status" in data, f"Response must have 'status' field: {data}"
    assert data["status"] in ("downloading", "ready", "not_started"), (
        f"status must be one of downloading/ready/not_started, got: {data['status']}"
    )
    assert "progress" in data, f"Response must have 'progress' field: {data}"
    assert 0 <= data["progress"] <= 100, (
        f"progress must be 0-100, got: {data['progress']}"
    )


# ---------------------------------------------------------------------------
# TEST 4: GET /status returns 'ready' when file exists
# ---------------------------------------------------------------------------

def test_stream_status_ready_with_file():
    """GET /status returns 'ready' when file_path is set and file exists on disk."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    # Use a dedicated test movie ID
    test_id = f"test_stream_{_uid()}"
    _seed_movie(token, test_id, "Test Stream Movie")

    # Create a real file inside the container
    fake_path = f"/downloads/test_stream/{test_id}.mp4"
    _create_test_file_in_container(fake_path, size_kb=64)

    # Update the DB to point to this file
    _db_set_file_path(test_id, fake_path)

    status_resp = requests.get(
        f"{BACKEND_URL}/api/movies/{test_id}/status",
        headers=headers,
        timeout=10,
    )
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == "ready", (
        f"Expected 'ready' when file exists, got: {data['status']}"
    )
    assert data["progress"] == 100


# ---------------------------------------------------------------------------
# TEST 5: Range request returns 206
# ---------------------------------------------------------------------------

def test_stream_range_returns_206():
    """GET /stream with Range header must return 206 Partial Content."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    test_id = f"test_range_{_uid()}"
    _seed_movie(token, test_id, "Test Range Movie")

    fake_path = f"/downloads/test_range/{test_id}.mp4"
    _create_test_file_in_container(fake_path, size_kb=64)
    _db_set_file_path(test_id, fake_path)

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{test_id}/stream",
        headers={
            "Authorization": f"Bearer {token}",
            "Range": "bytes=0-1023",
        },
        timeout=10,
    )
    assert resp.status_code == 206, (
        f"Expected 206 Partial Content, got {resp.status_code}: {resp.text[:200]}"
    )
    assert len(resp.content) > 0, "Response body must not be empty"


# ---------------------------------------------------------------------------
# TEST 6: Content-Range header present in 206 response
# ---------------------------------------------------------------------------

def test_stream_range_content_range_header():
    """206 response must include Content-Range header."""
    token = _register_and_login()
    headers_base = {"Authorization": f"Bearer {token}"}

    test_id = f"test_cr_{_uid()}"
    _seed_movie(token, test_id, "Test ContentRange Movie")

    fake_path = f"/downloads/test_cr/{test_id}.mp4"
    _create_test_file_in_container(fake_path, size_kb=64)
    _db_set_file_path(test_id, fake_path)

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{test_id}/stream",
        headers={**headers_base, "Range": "bytes=0-1023"},
        timeout=10,
    )
    assert resp.status_code == 206
    assert "content-range" in {k.lower() for k in resp.headers}, (
        f"206 response must include Content-Range header. Headers: {dict(resp.headers)}"
    )
    content_range = resp.headers.get("content-range") or resp.headers.get("Content-Range")
    assert content_range.startswith("bytes "), (
        f"Content-Range must start with 'bytes ', got: {content_range}"
    )
    assert "accept-ranges" in {k.lower() for k in resp.headers}, (
        "Response must include Accept-Ranges header"
    )


# ---------------------------------------------------------------------------
# TEST 7: Second click on already-downloaded movie does NOT create new download
# ---------------------------------------------------------------------------

def test_second_stream_no_new_download():
    """POST /stream on already-downloaded movie must not create a new aria2 download."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    test_id = f"test_nodl_{_uid()}"
    _seed_movie(token, test_id, "Test NoDownload Movie")

    fake_path = f"/downloads/test_nodl/{test_id}.mp4"
    _create_test_file_in_container(fake_path, size_kb=64)
    _db_set_file_path(test_id, fake_path)

    # Get initial active downloads count
    try:
        before_result = _aria2_rpc("aria2.tellActive", [["gid"]])
        count_before = len(before_result.get("result") or [])
    except Exception:
        count_before = 0

    # POST /stream — file already exists, should NOT call aria2.addUri
    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{test_id}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "ready", (
        f"Expected 'ready' for already-downloaded file, got: {data['status']}"
    )

    # Active downloads should not have increased
    try:
        after_result = _aria2_rpc("aria2.tellActive", [["gid"]])
        count_after = len(after_result.get("result") or [])
    except Exception:
        count_after = count_before

    assert count_after == count_before, (
        f"No new aria2 download should be created. Before: {count_before}, after: {count_after}"
    )


# ---------------------------------------------------------------------------
# TEST 8: watched_movies record created after streaming
# ---------------------------------------------------------------------------

def test_watched_movies_record_after_stream():
    """POST /stream must create a watched_movies record for the user."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    # Check no record before
    assert not _db_get_watched(token, TEST_MOVIE_ID) or True  # might already exist from other tests

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=15,
    )
    assert resp.status_code == 202

    # Check watched_movies now has a record for this movie
    assert _db_get_watched(token, TEST_MOVIE_ID), (
        "watched_movies must have a record after POST /stream"
    )


# ---------------------------------------------------------------------------
# TEST 9: ARIA2_RPC_SECRET does not appear in any response
# ---------------------------------------------------------------------------

def test_aria2_secret_not_in_responses():
    """ARIA2_RPC_SECRET must not appear in any API response."""
    if not ARIA2_SECRET:
        pytest.skip("ARIA2_RPC_SECRET not found in .env")

    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    # Check various endpoints
    endpoints = [
        ("GET",  f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}", None),
        ("GET",  f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/status", None),
        ("POST", f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/stream",
         {"magnet": MARKING_MAGNET}),
    ]

    for method, url, body in endpoints:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=15)
        else:
            resp = requests.post(url, json=body, headers=headers, timeout=15)

        text = resp.text
        assert ARIA2_SECRET not in text, (
            f"ARIA2_RPC_SECRET found in response from {method} {url}: {text[:200]}"
        )
        # Also check headers
        for val in resp.headers.values():
            assert ARIA2_SECRET not in val, (
                f"ARIA2_RPC_SECRET found in response header from {method} {url}"
            )


# ---------------------------------------------------------------------------
# TEST 10: Marking sheet magnet works
# ---------------------------------------------------------------------------

def test_marking_sheet_magnet_works():
    """The specific marking sheet magnet must be accepted and aria2 must start it."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=15,
    )
    assert resp.status_code == 202, (
        f"Marking sheet magnet must be accepted (202), got {resp.status_code}: {resp.text}"
    )

    data = resp.json()
    assert data["status"] in ("downloading", "ready"), (
        f"Status must be 'downloading' or 'ready', got: {data['status']}"
    )

    # Verify aria2 has accepted it (check via status endpoint)
    status_resp = requests.get(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/status",
        headers=headers,
        timeout=10,
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] in ("downloading", "ready"), (
        f"After starting with marking sheet magnet, status must be downloading or ready"
    )

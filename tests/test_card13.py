"""
CARD 13 — MKV → MP4 Conversion with FFmpeg

Tests:
  - GET /status returns 'converting' when is_converting is TRUE in DB
  - GET /stream returns 503 when file is non-native (mkv) and mp4 not ready
  - Non-native file_path triggers conversion (is_converting becomes TRUE in DB)
  - POST /stream returns 'converting' when is_converting is TRUE
  - After ffmpeg conversion completes, GET /status returns 'ready'
  - GET /stream serves mp4 after conversion completes
  - Marking sheet magnet (MKV) is eventually streamable

Requires the stack to be running with ffmpeg installed in backend container:
    docker-compose up -d
    uv run pytest tests/test_card13.py -v
"""

import os
import subprocess
import time
import uuid as uuid_mod

import psycopg2
import pytest
import requests

BACKEND_URL = "http://localhost:8000"
DB_DSN = "host=localhost port=5432 dbname=hypertube user=hypertube password=hypertube_dev_pass"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL = f"{BACKEND_URL}/api/auth/login"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MARKING_MAGNET = (
    "magnet:?xt=urn:btih:79816060ea56d56f2a2148cd45705511079f9bca"
    "&dn=TPB.AFK.2013.720p.h264-SimonKlose"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> str:
    s = _uid()
    email = f"test13_{s}@example.com"
    username = f"u13_{s}"
    password = f"Secure1_{s}"
    files = {
        "email": (None, email),
        "username": (None, username),
        "first_name": (None, "Test"),
        "last_name": (None, "User"),
        "password": (None, password),
    }
    r = requests.post(REGISTER_URL, files=files)
    assert r.status_code == 201, f"Register failed: {r.text}"
    r2 = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r2.status_code == 200, f"Login failed: {r2.text}"
    return r2.json()["token"]


def _seed_movie(token: str, movie_id: str, title: str = "Test Movie") -> None:
    """Ensure a movie exists in the DB."""
    requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}?title={title}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def _create_test_file_in_container(path: str, size_kb: int = 64) -> None:
    """Create a fake file in the aria2c container (which owns /downloads)."""
    dir_path = os.path.dirname(path)
    subprocess.run(
        ["docker-compose", "exec", "-T", "aria2c", "sh", "-c",
         f"mkdir -p {dir_path} && dd if=/dev/urandom bs=1024 count={size_kb} of={path} 2>/dev/null"],
        check=True,
        cwd=PROJECT_ROOT,
    )


def _create_real_mkv_in_container(path: str) -> bool:
    """Create a minimal valid .mkv file using ffmpeg in the backend container.
    Returns True if successful, False if ffmpeg not available."""
    dir_path = os.path.dirname(path)
    result = subprocess.run(
        ["docker-compose", "exec", "-T", "backend", "sh", "-c",
         f"mkdir -p {dir_path} && ffmpeg -f lavfi -i color=c=black:s=64x64:d=1 -c:v libx264 -y {path} 2>/dev/null"],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )
    return result.returncode == 0


def _db_set_file_path(movie_id_str: str, file_path: str) -> None:
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


def _db_set_is_converting(movie_id_str: str, value: bool) -> None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE movies SET is_converting = %s WHERE imdb_id = %s",
                (value, movie_id_str),
            )
        conn.commit()
    finally:
        conn.close()


def _db_get_is_converting(movie_id_str: str) -> bool:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT is_converting FROM movies WHERE imdb_id = %s",
                (movie_id_str,),
            )
            row = cur.fetchone()
            return bool(row[0]) if row else False
    finally:
        conn.close()


def _db_get_file_path(movie_id_str: str) -> str | None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path FROM movies WHERE imdb_id = %s",
                (movie_id_str,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TEST 1: GET /status returns 'converting' when is_converting = TRUE in DB
# ---------------------------------------------------------------------------

def test_status_returns_converting():
    """GET /status must return 'converting' when is_converting is TRUE."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_conv_{_uid()}"
    _seed_movie(token, movie_id)

    # Manually set is_converting = TRUE
    _db_set_is_converting(movie_id, True)

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/status",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "converting", (
        f"Expected 'converting', got: {data['status']}"
    )

    # Cleanup
    _db_set_is_converting(movie_id, False)


# ---------------------------------------------------------------------------
# TEST 2: GET /stream returns 503 when file is non-native and mp4 not ready
# ---------------------------------------------------------------------------

def test_serve_stream_503_during_conversion():
    """GET /stream must return 503 when file is .mkv and .mp4 not available."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_503_{_uid()}"
    _seed_movie(token, movie_id)

    # Create a fake .mkv file (won't convert, but we just test the 503)
    mkv_path = f"/downloads/test13/{movie_id}.mkv"
    _create_test_file_in_container(mkv_path)
    _db_set_file_path(movie_id, mkv_path)
    _db_set_is_converting(movie_id, True)  # Simulate conversion in progress

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/stream",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 503, (
        f"Expected 503 Service Unavailable during conversion, got {resp.status_code}: {resp.text[:200]}"
    )

    # Cleanup
    _db_set_is_converting(movie_id, False)


# ---------------------------------------------------------------------------
# TEST 3: Non-native file_path triggers conversion (is_converting becomes TRUE)
# ---------------------------------------------------------------------------

def test_mkv_file_triggers_conversion():
    """GET /status with a non-native file must trigger conversion and return 'converting'."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_mkv_{_uid()}"
    _seed_movie(token, movie_id)

    # Create a fake .mkv file (random bytes, not a real video)
    mkv_path = f"/downloads/test13/{movie_id}.mkv"
    _create_test_file_in_container(mkv_path)
    _db_set_file_path(movie_id, mkv_path)

    # Ensure is_converting is FALSE before
    assert not _db_get_is_converting(movie_id), "is_converting should be FALSE before test"

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/status",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # Should be 'converting' because mkv exists but mp4 does not
    assert data["status"] == "converting", (
        f"Expected 'converting' for non-native file without mp4, got: {data['status']}"
    )

    # is_converting should be TRUE in DB (conversion was claimed)
    assert _db_get_is_converting(movie_id), (
        "is_converting must be TRUE after triggering conversion"
    )

    # Cleanup: reset flag (ffmpeg will likely fail on fake file)
    time.sleep(2)
    _db_set_is_converting(movie_id, False)


# ---------------------------------------------------------------------------
# TEST 4: POST /stream returns 'converting' when is_converting = TRUE
# ---------------------------------------------------------------------------

def test_start_stream_returns_converting():
    """POST /stream must return status 'converting' when is_converting is TRUE."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_start_{_uid()}"
    _seed_movie(token, movie_id)

    # Set fake mkv file_path and is_converting = TRUE
    mkv_path = f"/downloads/test13/{movie_id}.mkv"
    _create_test_file_in_container(mkv_path)
    _db_set_file_path(movie_id, mkv_path)
    _db_set_is_converting(movie_id, True)

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{movie_id}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "converting", (
        f"Expected 'converting' when is_converting=TRUE, got: {data['status']}"
    )

    # Cleanup
    _db_set_is_converting(movie_id, False)


# ---------------------------------------------------------------------------
# TEST 5: After FFmpeg conversion, GET /status returns 'ready'
# ---------------------------------------------------------------------------

def test_status_ready_after_conversion():
    """GET /status returns 'ready' when converted .mp4 exists alongside .mkv."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_done_{_uid()}"
    _seed_movie(token, movie_id)

    # Simulate completed conversion: .mkv exists, .mp4 also exists
    mkv_path = f"/downloads/test13/{movie_id}.mkv"
    mp4_path = f"/downloads/test13/{movie_id}.mp4"
    _create_test_file_in_container(mkv_path)
    _create_test_file_in_container(mp4_path)  # mp4 "already converted"
    _db_set_file_path(movie_id, mkv_path)  # file_path still points to mkv

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/status",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ready", (
        f"Expected 'ready' when mp4 output exists, got: {data['status']}"
    )
    assert data["progress"] == 100


# ---------------------------------------------------------------------------
# TEST 6: GET /stream serves mp4 after conversion complete
# ---------------------------------------------------------------------------

def test_serve_stream_serves_mp4_after_conversion():
    """GET /stream must serve the .mp4 file when conversion is done."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_serve_{_uid()}"
    _seed_movie(token, movie_id)

    # Create both .mkv (original) and .mp4 (converted output)
    mkv_path = f"/downloads/test13/{movie_id}.mkv"
    mp4_path = f"/downloads/test13/{movie_id}.mp4"
    _create_test_file_in_container(mkv_path)
    _create_test_file_in_container(mp4_path, size_kb=64)
    # file_path points to .mkv but .mp4 exists
    _db_set_file_path(movie_id, mkv_path)

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/stream",
        headers={**headers, "Range": "bytes=0-1023"},
        timeout=10,
    )
    assert resp.status_code == 206, (
        f"Expected 206 (serving mp4), got {resp.status_code}: {resp.text[:200]}"
    )
    ct = resp.headers.get("content-type", "")
    assert "video/mp4" in ct, (
        f"Content-Type must be video/mp4 for converted file, got: {ct}"
    )


# ---------------------------------------------------------------------------
# TEST 7: Real FFmpeg conversion produces a playable .mp4
# ---------------------------------------------------------------------------

def test_real_ffmpeg_conversion():
    """Create a real .mkv, trigger conversion, wait for .mp4 to appear."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test13_real_{_uid()}"
    _seed_movie(token, movie_id)

    mkv_path = f"/downloads/test13_real/{movie_id}.mkv"
    mp4_path = f"/downloads/test13_real/{movie_id}.mp4"

    # Create a real .mkv using ffmpeg in the backend container
    ok = _create_real_mkv_in_container(mkv_path)
    if not ok:
        pytest.skip("ffmpeg not available in backend container — skipping real conversion test")

    _db_set_file_path(movie_id, mkv_path)

    # Trigger conversion via GET /status
    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/status",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "converting", (
        f"Expected 'converting' after triggering with real mkv, got: {data['status']}"
    )

    # Poll until 'ready' or timeout (60 seconds)
    deadline = time.time() + 60
    final_status = None
    while time.time() < deadline:
        time.sleep(3)
        r = requests.get(
            f"{BACKEND_URL}/api/movies/{movie_id}/status",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            final_status = r.json().get("status")
            if final_status == "ready":
                break

    assert final_status == "ready", (
        f"Expected status 'ready' after ffmpeg conversion within 60s, got: {final_status}"
    )

    # Verify the file_path in DB is now the .mp4
    db_path = _db_get_file_path(movie_id)
    assert db_path == mp4_path, (
        f"Expected file_path to be {mp4_path}, got: {db_path}"
    )

    # Verify streaming works
    stream_resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/stream",
        headers={**headers, "Range": "bytes=0-1023"},
        timeout=10,
    )
    assert stream_resp.status_code == 206, (
        f"Expected 206 after conversion, got {stream_resp.status_code}"
    )

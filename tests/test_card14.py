"""
CARD 14 — Limpeza Automática de Vídeos (Automatic Video Cleanup)

Tests:
  - Video with last_watched_at = 31 days ago is deleted by the cleanup job
  - Video with last_watched_at = 29 days ago is NOT deleted
  - After cleanup, file_path in DB is NULL for deleted videos
  - After deletion, attempting to watch reinitates download (new aria2 gid)
  - Cleanup does not fail if file_path already missing from disk (silent error)

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card14.py -v
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
CLEANUP_URL = f"{BACKEND_URL}/api/admin/cleanup"

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
    email = f"test14_{s}@example.com"
    username = f"u14_{s}"
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
    """Create a file in the aria2c container (owns /downloads)."""
    dir_path = os.path.dirname(path)
    subprocess.run(
        ["docker-compose", "exec", "-T", "aria2c", "sh", "-c",
         f"mkdir -p {dir_path} && dd if=/dev/urandom bs=1024 count={size_kb} of={path} 2>/dev/null"],
        check=True,
        cwd=PROJECT_ROOT,
    )


def _file_exists_in_container(path: str) -> bool:
    """Check whether a file exists inside the aria2c container."""
    result = subprocess.run(
        ["docker-compose", "exec", "-T", "aria2c", "sh", "-c", f"test -f {path} && echo yes || echo no"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return result.stdout.strip() == "yes"


def _db_set_file_path(movie_id_str: str, file_path: str | None) -> None:
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


def _db_set_last_watched_at(movie_id_str: str, days_ago: int) -> None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE movies SET last_watched_at = NOW() - INTERVAL '%s days' WHERE imdb_id = %s",
                (days_ago, movie_id_str),
            )
        conn.commit()
    finally:
        conn.close()


def _db_get_file_path(movie_id_str: str) -> str | None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_path FROM movies WHERE imdb_id = %s", (movie_id_str,))
            row = cur.fetchone()
            return row[0] if row else None
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


def _trigger_cleanup() -> None:
    """Call POST /api/admin/cleanup to run the job immediately."""
    resp = requests.post(CLEANUP_URL, timeout=30)
    assert resp.status_code == 200, f"Cleanup endpoint failed: {resp.text}"


# ---------------------------------------------------------------------------
# TEST 1: Video older than 30 days is deleted by cleanup job
# ---------------------------------------------------------------------------

def test_cleanup_deletes_old_video():
    """Cleanup job must delete file and clear file_path for videos > 30 days old."""
    token = _register_and_login()

    movie_id = f"test14_old_{_uid()}"
    _seed_movie(token, movie_id)

    # Create a real file in the container
    file_path = f"/downloads/test14/{movie_id}.mp4"
    _create_test_file_in_container(file_path)

    # Point the movie at this file with last_watched_at = 31 days ago
    _db_set_file_path(movie_id, file_path)
    _db_set_last_watched_at(movie_id, 31)

    # Verify file exists before cleanup
    assert _file_exists_in_container(file_path), "File must exist before cleanup"

    # Run cleanup
    _trigger_cleanup()

    # Verify file is gone
    assert not _file_exists_in_container(file_path), (
        f"File {file_path} must be deleted after cleanup for videos > 30 days old"
    )


# ---------------------------------------------------------------------------
# TEST 2: Video watched 29 days ago is NOT deleted
# ---------------------------------------------------------------------------

def test_cleanup_keeps_recent_video():
    """Cleanup job must NOT delete files for videos watched within 30 days."""
    token = _register_and_login()

    movie_id = f"test14_recent_{_uid()}"
    _seed_movie(token, movie_id)

    file_path = f"/downloads/test14/{movie_id}.mp4"
    _create_test_file_in_container(file_path)

    _db_set_file_path(movie_id, file_path)
    _db_set_last_watched_at(movie_id, 29)

    _trigger_cleanup()

    assert _file_exists_in_container(file_path), (
        f"File {file_path} must NOT be deleted for videos watched 29 days ago"
    )


# ---------------------------------------------------------------------------
# TEST 3: After cleanup, file_path in DB is NULL
# ---------------------------------------------------------------------------

def test_file_path_null_after_cleanup():
    """After cleanup deletes a file, movies.file_path must be set to NULL."""
    token = _register_and_login()

    movie_id = f"test14_null_{_uid()}"
    _seed_movie(token, movie_id)

    file_path = f"/downloads/test14/{movie_id}.mp4"
    _create_test_file_in_container(file_path)

    _db_set_file_path(movie_id, file_path)
    _db_set_last_watched_at(movie_id, 31)

    _trigger_cleanup()

    db_path = _db_get_file_path(movie_id)
    assert db_path is None, (
        f"file_path must be NULL in DB after cleanup, got: {db_path}"
    )


# ---------------------------------------------------------------------------
# TEST 4: After deletion, POST /stream reinitiates download via aria2
# ---------------------------------------------------------------------------

def test_deleted_video_reinitiates_download():
    """After cleanup clears file_path, POST /stream must start a new aria2 download."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test14_redl_{_uid()}"
    _seed_movie(token, movie_id)

    file_path = f"/downloads/test14/{movie_id}.mp4"
    _create_test_file_in_container(file_path)

    _db_set_file_path(movie_id, file_path)
    _db_set_last_watched_at(movie_id, 31)

    # Run cleanup — removes file and sets file_path = NULL
    _trigger_cleanup()

    db_path = _db_get_file_path(movie_id)
    assert db_path is None, "file_path must be NULL after cleanup"

    # Now try to stream: should reinitiate the download
    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{movie_id}/stream",
        json={"magnet": MARKING_MAGNET},
        headers=headers,
        timeout=15,
    )
    assert resp.status_code == 202, (
        f"Expected 202 when restarting deleted video, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert data["status"] in ("downloading", "ready"), (
        f"Expected 'downloading' or 'ready' after reinitiation, got: {data['status']}"
    )

    # Verify a new gid was created
    new_gid = _db_get_aria2_gid(movie_id)
    assert new_gid is not None, "A new aria2_gid must be stored after restarting download"


# ---------------------------------------------------------------------------
# TEST 5: Cleanup does not fail if file_path is already missing from disk
# ---------------------------------------------------------------------------

def test_cleanup_silent_on_missing_file():
    """Cleanup must complete without error when file_path points to a missing file."""
    token = _register_and_login()

    movie_id = f"test14_miss_{_uid()}"
    _seed_movie(token, movie_id)

    # Set a non-existent file path and old last_watched_at
    nonexistent_path = f"/downloads/test14/nonexistent_{movie_id}.mp4"
    _db_set_file_path(movie_id, nonexistent_path)
    _db_set_last_watched_at(movie_id, 31)

    # Cleanup must run without raising an error
    _trigger_cleanup()  # would throw AssertionError if cleanup returns non-200

    # file_path must be cleared even if the file didn't exist
    db_path = _db_get_file_path(movie_id)
    assert db_path is None, (
        f"file_path must be NULL even when file was already missing, got: {db_path}"
    )

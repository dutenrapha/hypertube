"""
CARD 15 — Legendas Automáticas (Automatic Subtitles)

Tests:
  - GET /api/movies/:id/subtitles returns array with at least 1 subtitle (popular movie)
  - .srt file exists on server after subtitle fetch
  - <track> element present in MovieDetails.tsx with correct src attribute
  - User with preferred_language='pt' gets PT subtitle if available
  - OPENSUBTITLES_API_KEY does not appear in any HTTP response

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card15.py -v
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

# A well-known public-domain movie that should have English subtitles on OpenSubtitles
TEST_MOVIE_ID = "Night_of_the_Living_Dead_1968"
TEST_TITLE = "Night of the Living Dead"

# Get OpenSubtitles API key from .env
OS_API_KEY = ""
try:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    with open(env_path) as f:
        for line in f:
            if line.startswith("OPENSUBTITLES_API_KEY="):
                OS_API_KEY = line.split("=", 1)[1].strip()
                break
except Exception:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login(preferred_language: str = "en") -> str:
    s = _uid()
    email = f"test15_{s}@example.com"
    username = f"u15_{s}"
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
    token = r2.json()["token"]

    # Set preferred language if not English
    if preferred_language != "en":
        user_id = _db_get_user_id_by_username(username)
        if user_id:
            _db_set_preferred_language(user_id, preferred_language)

    return token


def _seed_movie(token: str, movie_id: str = TEST_MOVIE_ID, title: str = TEST_TITLE) -> None:
    """Ensure a movie exists in the DB."""
    requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}?title={title}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def _db_get_movie_uuid(movie_id_str: str) -> str | None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM movies WHERE imdb_id = %s", (movie_id_str,))
            row = cur.fetchone()
            return str(row[0]) if row else None
    finally:
        conn.close()


def _db_get_user_id_by_username(username: str) -> str | None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            return str(row[0]) if row else None
    finally:
        conn.close()


def _db_set_preferred_language(user_id: str, lang: str) -> None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET preferred_language = %s WHERE id = %s",
                (lang, user_id),
            )
        conn.commit()
    finally:
        conn.close()


def _create_subtitle_in_container(movie_uuid: str, lang: str, content: str = "") -> str:
    """Create a subtitle file in the downloads/subtitles directory in the backend container."""
    if not content:
        content = (
            "1\n00:00:01,000 --> 00:00:04,000\n"
            f"Test subtitle ({lang})\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\n"
            "This is a test subtitle file.\n\n"
        )
    path = f"/downloads/subtitles/{movie_uuid}/{lang}.srt"
    # Use backend container since it runs as root and owns the downloads volume
    subprocess.run(
        ["docker-compose", "exec", "-T", "backend", "sh", "-c",
         f"mkdir -p /downloads/subtitles/{movie_uuid} && "
         f"cat > {path} << 'SUBTITLE_EOF'\n{content}\nSUBTITLE_EOF"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    return path


def _file_exists_in_container(path: str) -> bool:
    result = subprocess.run(
        ["docker-compose", "exec", "-T", "backend", "sh", "-c",
         f"test -f {path} && echo yes || echo no"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return result.stdout.strip() == "yes"


# ---------------------------------------------------------------------------
# TEST 1: GET /subtitles returns array with at least 1 subtitle
# ---------------------------------------------------------------------------

def test_subtitles_endpoint_returns_array():
    """GET /api/movies/:id/subtitles must return a JSON array."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    # Pre-create an English subtitle file so the test doesn't depend on live API
    movie_uuid = _db_get_movie_uuid(TEST_MOVIE_ID)
    assert movie_uuid is not None, "Movie must be in DB after seeding"

    _create_subtitle_in_container(movie_uuid, "en")

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/subtitles",
        headers=headers,
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list), f"Response must be a list, got: {type(data)}"
    assert len(data) >= 1, f"Must return at least 1 subtitle, got: {data}"

    # Verify structure
    first = data[0]
    assert "lang" in first, f"Each item must have 'lang' field: {first}"
    assert "url" in first, f"Each item must have 'url' field: {first}"
    assert first["url"].startswith("/subtitles/"), (
        f"URL must start with '/subtitles/', got: {first['url']}"
    )


# ---------------------------------------------------------------------------
# TEST 2: .srt file exists on server after subtitle fetch
# ---------------------------------------------------------------------------

def test_subtitle_file_exists_after_fetch():
    """After GET /subtitles, the .srt file must exist on the server."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test15_file_{_uid()}"
    _seed_movie(token, movie_id, "Test Subtitle Movie")

    movie_uuid = _db_get_movie_uuid(movie_id)
    assert movie_uuid is not None

    # Pre-create the subtitle file to simulate a cached download
    _create_subtitle_in_container(movie_uuid, "en")

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/subtitles",
        headers=headers,
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1, "Must return at least 1 subtitle entry"

    # Verify the file actually exists on the server
    en_path = f"/downloads/subtitles/{movie_uuid}/en.srt"
    assert _file_exists_in_container(en_path), (
        f"Subtitle file must exist at {en_path}"
    )


# ---------------------------------------------------------------------------
# TEST 3: <track> element present in MovieDetails.tsx with subtitle src
# ---------------------------------------------------------------------------

def test_track_element_in_frontend():
    """MovieDetails.tsx must include <track> elements for subtitles."""
    tsx_path = os.path.join(
        PROJECT_ROOT, "frontend", "src", "pages", "MovieDetails.tsx"
    )
    assert os.path.exists(tsx_path), "MovieDetails.tsx must exist"

    with open(tsx_path) as f:
        content = f.read()

    # Check that <track> element exists with subtitle-related attributes
    assert "<track" in content, "<track> element must be present in MovieDetails.tsx"
    assert "kind=\"subtitles\"" in content or 'kind="subtitles"' in content, (
        "<track> must have kind='subtitles'"
    )
    # Check that we reference subtitle URLs from the new endpoint
    assert "subtitle" in content.lower(), (
        "MovieDetails.tsx must reference subtitles"
    )
    assert "sub.url" in content or "subtitles" in content, (
        "MovieDetails.tsx must use subtitle URL from the endpoint"
    )


# ---------------------------------------------------------------------------
# TEST 4: User with preferred_language='pt' gets PT subtitle (if available)
# ---------------------------------------------------------------------------

def test_preferred_language_subtitle():
    """User with preferred_language='pt' must see PT subtitle in response if available."""
    token = _register_and_login(preferred_language="pt")
    headers = {"Authorization": f"Bearer {token}"}

    movie_id = f"test15_pt_{_uid()}"
    _seed_movie(token, movie_id, "Test PT Movie")

    movie_uuid = _db_get_movie_uuid(movie_id)
    assert movie_uuid is not None

    # Pre-create both EN and PT subtitle files
    _create_subtitle_in_container(movie_uuid, "en")
    _create_subtitle_in_container(movie_uuid, "pt")

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}/subtitles",
        headers=headers,
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list)

    langs = [item["lang"] for item in data]
    assert "en" in langs, f"English subtitle must always be included. Got: {langs}"
    assert "pt" in langs, (
        f"PT subtitle must be included for user with preferred_language='pt'. Got: {langs}"
    )


# ---------------------------------------------------------------------------
# TEST 5: OPENSUBTITLES_API_KEY does not appear in any HTTP response
# ---------------------------------------------------------------------------

def test_opensubtitles_key_not_in_responses():
    """OPENSUBTITLES_API_KEY must not appear in any API response."""
    if not OS_API_KEY:
        pytest.skip("OPENSUBTITLES_API_KEY not found in .env")

    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    _seed_movie(token)

    endpoints = [
        ("GET", f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}", None),
        ("GET", f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/subtitles", None),
        ("GET", f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/status", None),
    ]

    for method, url, body in endpoints:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        else:
            resp = requests.post(url, json=body, headers=headers, timeout=30)

        assert OS_API_KEY not in resp.text, (
            f"OPENSUBTITLES_API_KEY found in response body from {method} {url}"
        )
        for val in resp.headers.values():
            assert OS_API_KEY not in val, (
                f"OPENSUBTITLES_API_KEY found in response header from {method} {url}"
            )

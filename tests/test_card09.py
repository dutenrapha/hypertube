"""
CARD 09 — Busca de Vídeos (Video Search)

Tests:
  - GET /api/search requires authentication (no token → 401)
  - GET /api/search?q=<query> returns a paginated JSON response with 'movies' array
  - Each movie item has the required fields: id, title, source
  - At least one result is from archive.org
  - Second call with same query is faster (served from Redis cache)
  - Empty query returns Archive.org popular results
  - search.rs references both supported sources (static code check)

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card09.py -v
"""

import os
import time
import uuid as uuid_mod

import pytest
import requests

BACKEND_URL = "http://localhost:8000"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL    = f"{BACKEND_URL}/api/auth/login"
SEARCH_URL   = f"{BACKEND_URL}/api/search"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> str:
    """Register a fresh user and return a valid JWT token."""
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


def _search(token: str, **kwargs) -> dict:
    """Helper: call search API and return parsed JSON."""
    resp = requests.get(
        SEARCH_URL,
        params=kwargs,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# TEST 1: Search requires authentication
# ---------------------------------------------------------------------------

def test_search_requires_auth():
    """GET /api/search without a token must return 401."""
    resp = requests.get(SEARCH_URL, params={"q": "documentary"}, timeout=15)
    assert resp.status_code == 401, (
        f"Expected 401 without auth, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# TEST 2: Search returns paginated JSON with 'movies' array
# ---------------------------------------------------------------------------

def test_search_returns_paginated_response():
    """GET /api/search?q=adventure returns 200 with paginated response."""
    token = _register_and_login()
    data = _search(token, q="adventure")

    assert "movies" in data, f"Response must have 'movies' key: {data.keys()}"
    assert isinstance(data["movies"], list), "movies must be a list"
    assert "page" in data
    assert "limit" in data
    assert "total" in data
    assert "has_next" in data


# ---------------------------------------------------------------------------
# TEST 3: Each movie has the required fields
# ---------------------------------------------------------------------------

def test_search_movie_items_have_required_fields():
    """Every movie item must have id, title, and source fields."""
    token = _register_and_login()
    data = _search(token, q="adventure")
    movies = data["movies"]

    if not movies:
        pytest.skip("Search returned 0 results (external service may be unavailable)")

    for movie in movies:
        assert "id" in movie,     f"Missing 'id' in movie: {movie}"
        assert "title" in movie,  f"Missing 'title' in movie: {movie}"
        assert "source" in movie, f"Missing 'source' in movie: {movie}"


# ---------------------------------------------------------------------------
# TEST 4: At least one result is from archive.org
# ---------------------------------------------------------------------------

def test_search_includes_archive_org_results():
    """At least one result must have source='archive.org'."""
    token = _register_and_login()
    data = _search(token, q="film")
    movies = data["movies"]

    if not movies:
        pytest.skip("Search returned 0 results (external service may be unavailable)")

    sources = {m.get("source") for m in movies}
    assert "archive.org" in sources, (
        f"Expected at least one archive.org result. Found sources: {sources}"
    )


# ---------------------------------------------------------------------------
# TEST 5: Empty query returns popular Archive.org results
# ---------------------------------------------------------------------------

def test_search_empty_query_returns_popular_movies():
    """GET /api/search?q= (empty) must return a non-empty list of movies."""
    token = _register_and_login()
    data = _search(token, q="")

    movies = data["movies"]
    assert isinstance(movies, list), "Response movies must be a JSON array"
    assert len(movies) > 0, "Empty query must return at least some popular movies"


# ---------------------------------------------------------------------------
# TEST 6: Second identical search is served from cache (faster)
# ---------------------------------------------------------------------------

def test_search_second_call_uses_cache():
    """
    The second call with the same query should be significantly faster
    because the result is served from Redis cache.
    """
    token = _register_and_login()
    query = f"cache_test_{_uid()}"

    headers = {"Authorization": f"Bearer {token}"}

    t0 = time.monotonic()
    resp1 = requests.get(SEARCH_URL, params={"q": query}, headers=headers, timeout=30)
    first_duration = time.monotonic() - t0
    assert resp1.status_code == 200

    t1 = time.monotonic()
    resp2 = requests.get(SEARCH_URL, params={"q": query}, headers=headers, timeout=15)
    second_duration = time.monotonic() - t1
    assert resp2.status_code == 200

    assert second_duration < 1.0, (
        f"Cached response should be < 1s, got {second_duration:.2f}s"
    )
    assert resp1.json() == resp2.json(), "Cached response must match first response"


# ---------------------------------------------------------------------------
# TEST 7: search.rs implements both sources (static code check)
# ---------------------------------------------------------------------------

def test_search_rs_implements_both_sources():
    """
    The search.rs source file must reference both archive.org and
    publicdomaintorrents.info, confirming both sources are integrated.
    """
    search_rs_path = os.path.join(
        PROJECT_ROOT, "backend", "src", "routes", "search.rs"
    )
    assert os.path.isfile(search_rs_path), "backend/src/routes/search.rs must exist"

    with open(search_rs_path) as f:
        source = f.read()

    assert "archive.org" in source, "search.rs must reference archive.org"
    assert "publicdomaintorrents" in source, "search.rs must reference publicdomaintorrents.info"
    assert "redis" in source.lower(), "search.rs must use Redis caching"

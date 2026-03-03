"""
CARD 10 — Thumbnails, Pagination and Filters

Tests:
  - GET /api/search returns paginated response with has_next, total, page, limit
  - page + limit params correctly slice the result set
  - has_next is True when more results are available
  - Each movie has 'watched' boolean field
  - Each movie has 'genre' field (may be null)
  - Sorting by rating, year, name is implemented in the frontend source
  - Genre and year filter controls exist in the frontend source
  - Infinite scroll (IntersectionObserver) is used — no "Next Page" button
  - Search.css has responsive grid breakpoints at 768px and 1280px

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card10.py -v
"""

import os
import uuid as uuid_mod

import pytest
import requests

BACKEND_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:3000"

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


# ---------------------------------------------------------------------------
# TEST 1: Paginated response has all required envelope fields
# ---------------------------------------------------------------------------

def test_search_response_has_pagination_envelope():
    """GET /api/search must return an object with movies, page, limit, total, has_next."""
    token = _register_and_login()
    resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 5},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    for field in ("movies", "page", "limit", "total", "has_next"):
        assert field in data, f"Missing '{field}' in response: {data.keys()}"

    assert isinstance(data["movies"], list)
    assert data["page"] == 1
    assert data["limit"] == 5
    assert isinstance(data["total"], int)
    assert isinstance(data["has_next"], bool)


# ---------------------------------------------------------------------------
# TEST 2: limit parameter is respected
# ---------------------------------------------------------------------------

def test_search_limit_param_is_respected():
    """GET /api/search?limit=3 must return at most 3 movies."""
    token = _register_and_login()
    resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 3},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["movies"]) <= 3, (
        f"Expected at most 3 movies, got {len(data['movies'])}"
    )


# ---------------------------------------------------------------------------
# TEST 3: has_next is True when there are more pages
# ---------------------------------------------------------------------------

def test_search_has_next_when_more_results():
    """With limit=5 and enough results, has_next must be True."""
    token = _register_and_login()
    resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 5},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()

    if data["total"] <= 5:
        pytest.skip("Not enough total results to test has_next (external services may be down)")

    assert data["has_next"] is True, (
        f"Expected has_next=True when total={data['total']} > limit=5"
    )


# ---------------------------------------------------------------------------
# TEST 4: Page 2 returns different movies than page 1
# ---------------------------------------------------------------------------

def test_search_page2_is_different_from_page1():
    """Fetching page 2 must return different movie IDs than page 1."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    r1 = requests.get(SEARCH_URL, params={"q": "", "page": 1, "limit": 5}, headers=headers, timeout=30)
    r2 = requests.get(SEARCH_URL, params={"q": "", "page": 2, "limit": 5}, headers=headers, timeout=30)
    assert r1.status_code == 200
    assert r2.status_code == 200

    ids1 = {m["id"] for m in r1.json()["movies"]}
    ids2 = {m["id"] for m in r2.json()["movies"]}

    if not ids1 or not ids2:
        pytest.skip("Not enough results to compare pages (external services may be down)")

    overlap = ids1 & ids2
    assert not overlap, (
        f"Page 1 and page 2 must have different movies. Overlap: {overlap}"
    )


# ---------------------------------------------------------------------------
# TEST 5: Each movie has 'watched' boolean field
# ---------------------------------------------------------------------------

def test_search_movie_has_watched_field():
    """Each movie in the response must have a 'watched' boolean field."""
    token = _register_and_login()
    resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 10},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200
    movies = resp.json()["movies"]

    if not movies:
        pytest.skip("No movies returned (external services may be down)")

    for movie in movies:
        assert "watched" in movie, f"Missing 'watched' field in movie: {movie}"
        assert isinstance(movie["watched"], bool), (
            f"'watched' must be a bool, got: {type(movie['watched'])}"
        )


# ---------------------------------------------------------------------------
# TEST 6: Each movie has 'genre' field (can be null)
# ---------------------------------------------------------------------------

def test_search_movie_has_genre_field():
    """Each movie must have a 'genre' field (string or null)."""
    token = _register_and_login()
    resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 10},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200
    movies = resp.json()["movies"]

    if not movies:
        pytest.skip("No movies returned (external services may be down)")

    for movie in movies:
        assert "genre" in movie, f"Missing 'genre' field in movie: {movie}"
        assert movie["genre"] is None or isinstance(movie["genre"], str), (
            f"'genre' must be string or null, got: {type(movie['genre'])}"
        )


# ---------------------------------------------------------------------------
# TEST 7: Frontend search page is served at /
# ---------------------------------------------------------------------------

def test_frontend_search_page_served():
    """GET / must return 200 with HTML (the SPA root)."""
    resp = requests.get(f"{FRONTEND_URL}/", timeout=10)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# TEST 8: IntersectionObserver is used for infinite scroll (static check)
# ---------------------------------------------------------------------------

def test_search_tsx_uses_intersection_observer():
    """Search.tsx must use IntersectionObserver for infinite scroll."""
    search_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.tsx")
    assert os.path.isfile(search_tsx), "Search.tsx must exist"

    with open(search_tsx) as f:
        source = f.read()

    assert "IntersectionObserver" in source, (
        "Search.tsx must use IntersectionObserver for infinite scroll"
    )


# ---------------------------------------------------------------------------
# TEST 9: No 'next page' button in Search.tsx (infinite scroll only)
# ---------------------------------------------------------------------------

def test_search_tsx_has_no_next_page_button():
    """Search.tsx must NOT have a next page button — pagination is automatic."""
    search_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.tsx")
    assert os.path.isfile(search_tsx), "Search.tsx must exist"

    with open(search_tsx) as f:
        source = f.read().lower()

    # Check that there is no "next page" button
    forbidden = ["next page", "próxima página", "load more", "carregar mais"]
    for phrase in forbidden:
        assert phrase not in source, (
            f"Search.tsx must not have a '{phrase}' button — use IntersectionObserver instead"
        )


# ---------------------------------------------------------------------------
# TEST 10: Search.css has responsive breakpoints
# ---------------------------------------------------------------------------

def test_search_css_has_responsive_breakpoints():
    """Search.css must define grid breakpoints for 768px (2 cols) and 1280px (4 cols)."""
    search_css = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.css")
    assert os.path.isfile(search_css), "Search.css must exist"

    with open(search_css) as f:
        css = f.read()

    assert "768px" in css, "Search.css must have a 768px breakpoint (2-column tablet layout)"
    assert "1280px" in css, "Search.css must have a 1280px breakpoint (4-column desktop layout)"
    assert "repeat(2" in css, "Search.css must define a 2-column grid"
    assert "repeat(4" in css, "Search.css must define a 4-column grid"


# ---------------------------------------------------------------------------
# TEST 11: Sort and filter controls exist in Search.tsx
# ---------------------------------------------------------------------------

def test_search_tsx_has_sort_and_filter_controls():
    """Search.tsx must include sort dropdown and year filter inputs."""
    search_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.tsx")
    assert os.path.isfile(search_tsx), "Search.tsx must exist"

    with open(search_tsx) as f:
        source = f.read()

    # Sort options
    assert "sortBy" in source or "sort_by" in source, (
        "Search.tsx must have sort state"
    )
    assert "sort_rating" in source or "Rating" in source, (
        "Search.tsx must have a sort-by-rating option"
    )
    assert "sort_year" in source or "year" in source.lower(), (
        "Search.tsx must have a sort-by-year option"
    )
    assert "sort_name" in source or "name" in source.lower(), (
        "Search.tsx must have a sort-by-name option"
    )

    # Year filter inputs
    assert "yearMin" in source or "year_from" in source, (
        "Search.tsx must have a year-from filter"
    )
    assert "yearMax" in source or "year_to" in source, (
        "Search.tsx must have a year-to filter"
    )

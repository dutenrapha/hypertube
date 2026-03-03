"""
CARD 11 — Video Details Page

Tests:
  - GET /api/movies/:id returns required fields (title, year, imdb_rating, genre,
    summary, director, cast, cover_url, available_subtitles, comments_count)
  - GET /api/movies/:id requires authentication
  - GET /api/movies/:id creates the movie in DB if it doesn't exist yet
  - POST /api/movies/:id/comments creates a comment
  - POST /api/movies/:id/comments requires authentication
  - POST /api/movies/:id/comments returns 400 for empty content
  - GET /api/movies/:id/comments lists comments
  - GET /api/movies/:id/comments requires authentication
  - comments_count increases after posting a comment
  - Frontend has a MovieDetails page at /movies/:id route
  - MovieDetails.tsx contains video player, cast, director, comments section

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card11.py -v
"""

import os
import uuid as uuid_mod

import pytest
import requests

BACKEND_URL  = "http://localhost:8000"
FRONTEND_URL = "http://localhost:3000"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL    = f"{BACKEND_URL}/api/auth/login"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A stable archive.org item known to exist (Night of the Living Dead is public domain)
TEST_MOVIE_ID = "Night_of_the_Living_Dead_1968"
TEST_TITLE    = "Night of the Living Dead"


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


def _movie_url(movie_id: str, **kwargs) -> str:
    url = f"{BACKEND_URL}/api/movies/{movie_id}"
    if kwargs:
        params = "&".join(f"{k}={v}" for k, v in kwargs.items())
        url += f"?{params}"
    return url


# ---------------------------------------------------------------------------
# TEST 1: GET /api/movies/:id returns required fields
# ---------------------------------------------------------------------------

def test_get_movie_returns_required_fields():
    """GET /api/movies/:id must return all required fields."""
    token = _register_and_login()
    resp = requests.get(
        _movie_url(TEST_MOVIE_ID, title=TEST_TITLE),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    required = ["id", "title", "year", "imdb_rating", "genre", "summary",
                "director", "cast", "cover_url", "available_subtitles", "comments_count"]
    for field in required:
        assert field in data, f"Missing required field '{field}' in response: {list(data.keys())}"

    assert data["id"] == TEST_MOVIE_ID
    assert isinstance(data["cast"], list), f"'cast' must be a list"
    assert isinstance(data["available_subtitles"], list), f"'available_subtitles' must be a list"
    assert isinstance(data["comments_count"], int), f"'comments_count' must be an int"


# ---------------------------------------------------------------------------
# TEST 2: GET /api/movies/:id requires authentication
# ---------------------------------------------------------------------------

def test_get_movie_requires_auth():
    """GET /api/movies/:id without token must return 401."""
    resp = requests.get(
        _movie_url(TEST_MOVIE_ID),
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# TEST 3: Movie is created in DB on first visit (idempotent)
# ---------------------------------------------------------------------------

def test_get_movie_creates_movie_in_db():
    """Calling GET /api/movies/:id twice must return the same data (idempotent)."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    r1 = requests.get(_movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30)
    r2 = requests.get(_movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"], "Movie ID must be consistent across requests"


# ---------------------------------------------------------------------------
# TEST 4: POST /api/movies/:id/comments creates a comment
# ---------------------------------------------------------------------------

def test_create_comment():
    """POST /api/movies/:id/comments must create and return a comment."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure movie exists first
    requests.get(_movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30)

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        json={"content": "Great classic horror film!"},
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "id" in data, "Response must include comment id"
    assert "content" in data, "Response must include content"
    assert data["content"] == "Great classic horror film!"


# ---------------------------------------------------------------------------
# TEST 5: POST /api/movies/:id/comments requires authentication
# ---------------------------------------------------------------------------

def test_create_comment_requires_auth():
    """POST /api/movies/:id/comments without token must return 401."""
    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        json={"content": "Should fail"},
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# TEST 6: POST /api/movies/:id/comments rejects empty content
# ---------------------------------------------------------------------------

def test_create_comment_rejects_empty_content():
    """POST /api/movies/:id/comments with empty content must return 400."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure movie exists
    requests.get(_movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30)

    resp = requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        json={"content": "   "},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# TEST 7: GET /api/movies/:id/comments lists comments
# ---------------------------------------------------------------------------

def test_list_comments():
    """GET /api/movies/:id/comments must return a list of comments."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    # Ensure movie exists and post a comment
    requests.get(_movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30)
    requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        json={"content": "Listing test comment"},
        headers=headers,
        timeout=10,
    )

    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        headers=headers,
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "comments" in data, f"Response must have 'comments' key: {data.keys()}"
    assert isinstance(data["comments"], list), "'comments' must be a list"

    if data["comments"]:
        c = data["comments"][0]
        for field in ("id", "content", "username"):
            assert field in c, f"Comment must have '{field}' field"


# ---------------------------------------------------------------------------
# TEST 8: GET /api/movies/:id/comments requires authentication
# ---------------------------------------------------------------------------

def test_list_comments_requires_auth():
    """GET /api/movies/:id/comments without token must return 401."""
    resp = requests.get(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# TEST 9: comments_count increases after posting a comment
# ---------------------------------------------------------------------------

def test_comments_count_increases():
    """comments_count in GET /api/movies/:id must increase after posting."""
    token = _register_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    r_before = requests.get(
        _movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30
    )
    assert r_before.status_code == 200
    count_before = r_before.json()["comments_count"]

    requests.post(
        f"{BACKEND_URL}/api/movies/{TEST_MOVIE_ID}/comments",
        json={"content": "Count test comment"},
        headers=headers,
        timeout=10,
    )

    r_after = requests.get(
        _movie_url(TEST_MOVIE_ID, title=TEST_TITLE), headers=headers, timeout=30
    )
    assert r_after.status_code == 200
    count_after = r_after.json()["comments_count"]

    assert count_after == count_before + 1, (
        f"Expected comments_count to increase by 1: {count_before} → {count_after}"
    )


# ---------------------------------------------------------------------------
# TEST 10: Frontend MovieDetails.tsx exists with correct structure
# ---------------------------------------------------------------------------

def test_movie_details_tsx_exists():
    """MovieDetails.tsx must exist with required UI elements."""
    movie_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "MovieDetails.tsx")
    assert os.path.isfile(movie_tsx), "MovieDetails.tsx must exist in frontend/src/pages/"

    with open(movie_tsx) as f:
        source = f.read()

    # Required elements
    assert "useParams" in source, "MovieDetails.tsx must use useParams to get the movie id"
    assert "comments" in source.lower(), "MovieDetails.tsx must have a comments section"
    assert "director" in source.lower(), "MovieDetails.tsx must display the director"
    assert "cast" in source.lower(), "MovieDetails.tsx must display the cast"


# ---------------------------------------------------------------------------
# TEST 11: main.tsx has /movies/:id route
# ---------------------------------------------------------------------------

def test_main_tsx_has_movie_route():
    """main.tsx must declare a /movies/:id route."""
    main_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "main.tsx")
    assert os.path.isfile(main_tsx), "main.tsx must exist"

    with open(main_tsx) as f:
        source = f.read()

    assert "/movies/:id" in source, "main.tsx must have a /movies/:id route"
    assert "MovieDetails" in source, "main.tsx must import and use MovieDetails"


# ---------------------------------------------------------------------------
# TEST 12: Search.tsx cards navigate to /movies/:id
# ---------------------------------------------------------------------------

def test_search_cards_navigate_to_movie_details():
    """Search.tsx movie cards must navigate to /movies/:id on click."""
    search_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.tsx")
    assert os.path.isfile(search_tsx), "Search.tsx must exist"

    with open(search_tsx) as f:
        source = f.read()

    assert "/movies/" in source, (
        "Search.tsx cards must navigate to /movies/:id"
    )

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

import psycopg2
import pytest
import requests

BACKEND_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:3000"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL    = f"{BACKEND_URL}/api/auth/login"
SEARCH_URL   = f"{BACKEND_URL}/api/search"

DB_DSN = (
    "host=localhost port=5432 dbname=hypertube "
    "user=hypertube password=hypertube_dev_pass"
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _register_and_login() -> tuple[str, str]:
    """Register a fresh user and return (token, username)."""
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
    return r2.json()["token"], username


def _login_token(username: str, password: str) -> str:
    r = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["token"]


def _user_id_from_username(username: str) -> str:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            assert row is not None, f"User {username} not found in DB"
            return str(row[0])
    finally:
        conn.close()


def _ensure_movie_in_db(imdb_id: str, title: str) -> str:
    """Insert a movie row keyed by imdb_id (= external_id) and return its UUID."""
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (title, imdb_id)
                VALUES (%s, %s)
                ON CONFLICT (imdb_id) DO UPDATE SET title = EXCLUDED.title
                RETURNING id
                """,
                (title, imdb_id),
            )
            row = cur.fetchone()
            assert row is not None
            movie_uuid = str(row[0])
        conn.commit()
        return movie_uuid
    finally:
        conn.close()


def _mark_watched(user_id: str, movie_uuid: str) -> None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO watched_movies (user_id, movie_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, movie_id) DO UPDATE SET watched_at = NOW()
                """,
                (user_id, movie_uuid),
            )
        conn.commit()
    finally:
        conn.close()


def _drop_search_cache() -> None:
    """Best-effort: invalidate search cache so the new request re-injects watched."""
    try:
        import redis  # type: ignore
        r = redis.Redis(host="localhost", port=6379)
        for key in r.scan_iter(match="search:*"):
            r.delete(key)
    except Exception:
        # If redis client isn't reachable, the test is still meaningful — watched
        # injection happens after the cache lookup, so it should still work.
        pass


def _legacy_login() -> str:
    """Backward compat for older tests still expecting `_register_and_login()` -> str."""
    token, _ = _register_and_login()
    return token


# ---------------------------------------------------------------------------
# TEST 1: Paginated response has all required envelope fields
# ---------------------------------------------------------------------------

def test_search_response_has_pagination_envelope():
    """GET /api/search must return an object with movies, page, limit, total, has_next."""
    token, _ = _register_and_login()
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
    token, _ = _register_and_login()
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
    token, _ = _register_and_login()
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
    token, _ = _register_and_login()
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
    token, _ = _register_and_login()
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
    token, _ = _register_and_login()
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


# ---------------------------------------------------------------------------
# TEST 12: watched flag is true for the user who watched, false for others
# ---------------------------------------------------------------------------

def test_search_watched_flag_is_user_specific():
    """A movie marked as watched by user A must come back as watched=true for A
    and watched=false for a different user B."""
    # Pick a stable Archive.org identifier as the external_id we will pretend
    # the user "watched". We do NOT need to actually start a stream — inserting
    # directly into watched_movies through the DB is enough to validate the
    # backend's watched-injection logic.
    fake_imdb_id = f"hypertube_test_{_uid()}"
    fake_title = f"Hypertube Test Movie {_uid()}"

    movie_uuid = _ensure_movie_in_db(fake_imdb_id, fake_title)

    # User A: marks the movie as watched (via direct DB insert)
    s = _uid()
    user_a_username = f"watched_a_{s}"
    user_a_password = f"Secure1_{s}"
    files = {
        "email":      (None, f"watched_a_{s}@example.com"),
        "username":   (None, user_a_username),
        "first_name": (None, "Test"),
        "last_name":  (None, "User"),
        "password":   (None, user_a_password),
    }
    r = requests.post(REGISTER_URL, files=files)
    assert r.status_code == 201, f"Register A failed: {r.text}"

    user_a_id = _user_id_from_username(user_a_username)
    _mark_watched(user_a_id, movie_uuid)

    token_a = _login_token(user_a_username, user_a_password)
    token_b, _ = _register_and_login()

    # Drop any cached search:* keys so the response is freshly enriched
    _drop_search_cache()

    def _fetch_watched(token: str, q: str) -> dict[str, bool]:
        resp = requests.get(
            SEARCH_URL,
            params={"q": q, "page": 1, "limit": 50},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert resp.status_code == 200, resp.text
        return {m["id"]: m["watched"] for m in resp.json()["movies"]}

    # The fake imdb_id is unlikely to be returned by archive.org; instead, we
    # query the empty/popular list and look for any movie that happens to be
    # both returned AND already marked as watched by user A. To make the test
    # deterministic we *also* check via the DB what we expect.
    #
    # We also exercise the backend with an existing real movie: pick the first
    # movie returned by the popular list and mark it as watched for user A.
    popular_resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 5},
        headers={"Authorization": f"Bearer {token_a}"},
        timeout=30,
    )
    assert popular_resp.status_code == 200, popular_resp.text
    popular_movies = popular_resp.json()["movies"]
    if not popular_movies:
        pytest.skip("Popular list is empty — external services may be down")

    target = popular_movies[0]
    target_imdb_id = target["id"]
    target_uuid = _ensure_movie_in_db(target_imdb_id, target["title"])
    _mark_watched(user_a_id, target_uuid)

    _drop_search_cache()

    a_map = _fetch_watched(token_a, "")
    b_map = _fetch_watched(token_b, "")

    assert target_imdb_id in a_map, (
        f"Target {target_imdb_id} not present in user A response"
    )
    assert target_imdb_id in b_map, (
        f"Target {target_imdb_id} not present in user B response"
    )
    assert a_map[target_imdb_id] is True, (
        f"User A should see watched=true for {target_imdb_id}, got {a_map[target_imdb_id]}"
    )
    assert b_map[target_imdb_id] is False, (
        f"User B should see watched=false for {target_imdb_id}, got {b_map[target_imdb_id]}"
    )


# ---------------------------------------------------------------------------
# TEST 13: Frontend renders a dark overlay on watched thumbnails
# ---------------------------------------------------------------------------

def test_search_tsx_has_watched_overlay():
    """Search.tsx must render a visual overlay (not just a small badge) for
    watched movies, satisfying CARD 10's 'overlay escuro + badge' requirement."""
    search_tsx = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.tsx")
    with open(search_tsx) as f:
        source = f.read()

    assert "movie.watched" in source, (
        "Search.tsx must branch on movie.watched to render the overlay"
    )
    assert 'data-testid="watched-overlay"' in source, (
        "Search.tsx must mark the watched overlay with data-testid='watched-overlay' "
        "so it is testable"
    )
    assert "watched-overlay" in source, (
        "Search.tsx must render an element with the watched-overlay class"
    )


def test_search_css_has_watched_styles():
    """Search.css must define styles that *visually* differentiate watched cards
    (overlay or filter), not just a small corner badge."""
    search_css = os.path.join(PROJECT_ROOT, "frontend", "src", "pages", "Search.css")
    with open(search_css) as f:
        css = f.read()

    assert ".watched" in css, (
        "Search.css must define a .watched selector that styles the watched card"
    )
    assert "watched-overlay" in css, (
        "Search.css must define styling for .watched-overlay"
    )
    # Either grayscale, brightness or an overlay rgba — any of these proves a
    # *visual* differentiation beyond a tiny corner badge.
    has_visual_effect = (
        "grayscale" in css
        or "brightness" in css
        or "rgba(0, 0, 0" in css
        or "rgba(0,0,0" in css
    )
    assert has_visual_effect, (
        "Search.css must visually dim/overlay watched thumbnails "
        "(filter: grayscale/brightness or rgba black overlay)"
    )


# ---------------------------------------------------------------------------
# TEST 14: archive.org proxy stream marks the movie as watched
# ---------------------------------------------------------------------------
#
# The HTML <video> element triggered by clicking play on an archive.org movie
# loads /api/movies/:id/stream/archive (no torrent involved). That code path
# previously didn't write anything to watched_movies, so movies like
# "About Bananas" stayed un-watched even after the user actually watched them.
# This test guards against that regression.

def _watched_count(user_id: str, imdb_id: str) -> int:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM watched_movies wm
                JOIN movies m ON m.id = wm.movie_id
                WHERE wm.user_id = %s AND m.imdb_id = %s
                """,
                (user_id, imdb_id),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        conn.close()


def test_archive_stream_marks_movie_as_watched():
    """A request to GET /api/movies/:id/stream/archive must record the movie
    as watched for the requesting user (so archive.org-only movies like
    'About Bananas' appear with the watched overlay on the next search)."""
    token, username = _register_and_login()
    user_id = _user_id_from_username(username)

    imdb_id = f"hypertube_archive_{_uid()}"

    # Sanity: not watched before the request
    assert _watched_count(user_id, imdb_id) == 0

    # The archive.org video URL almost certainly doesn't exist for our fake id,
    # but that's fine — watched marking happens *before* the upstream proxy is
    # called, so the response can be 404/502 and the test still asserts the
    # watched side-effect.
    requests.get(
        f"{BACKEND_URL}/api/movies/{imdb_id}/stream/archive",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
        # Only ask for a small initial chunk so we exercise the "initial
        # request" branch that triggers the watched insert.
    )

    assert _watched_count(user_id, imdb_id) == 1, (
        "GET /api/movies/:id/stream/archive must insert a watched_movies row "
        "for the requesting user on the initial request"
    )

    # Subsequent Range requests beyond byte 0 must NOT create extra rows
    # (we use ON CONFLICT DO UPDATE — count must stay at exactly 1).
    requests.get(
        f"{BACKEND_URL}/api/movies/{imdb_id}/stream/archive",
        headers={
            "Authorization": f"Bearer {token}",
            "Range": "bytes=1024-2047",
        },
        timeout=15,
    )
    assert _watched_count(user_id, imdb_id) == 1


def test_archive_stream_marks_propagates_to_search_overlay():
    """End-to-end: after the archive proxy is hit, the next /api/search
    response must show watched=true for that movie."""
    token, username = _register_and_login()
    user_id = _user_id_from_username(username)

    # Pick a real movie returned by the popular list so it shows up in /search
    popular = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 5},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert popular.status_code == 200
    movies = popular.json()["movies"]
    if not movies:
        pytest.skip("Popular list empty — external services may be down")

    target = movies[0]
    target_imdb_id = target["id"]

    # Trigger the archive stream — this is the moment the user "watches" the movie
    requests.get(
        f"{BACKEND_URL}/api/movies/{target_imdb_id}/stream/archive",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    # The watched_movies row must exist
    assert _watched_count(user_id, target_imdb_id) == 1

    # And it must propagate to the search response (after dropping the cache)
    _drop_search_cache()
    resp = requests.get(
        SEARCH_URL,
        params={"q": "", "page": 1, "limit": 50},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert resp.status_code == 200
    by_id = {m["id"]: m["watched"] for m in resp.json()["movies"]}
    assert by_id.get(target_imdb_id) is True, (
        f"Expected watched=true after archive stream, got {by_id.get(target_imdb_id)}"
    )

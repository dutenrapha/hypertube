"""
CARD 16 — API RESTful com OAuth2 Server (Bonus)

Tests:
  - POST /oauth/token with valid client_id and client_secret returns access_token
  - GET /users without Bearer token returns 401
  - GET /users with valid Bearer token returns array of users
  - GET /movies returns list with id and name
  - GET /movies/:id returns all mandatory fields
  - POST /comments creates comment and returns 201
  - DELETE /comments/:id from another user returns 403
  - GET /nonexistent route returns 404
  - PUT /users returns 405 Method Not Allowed
  - expired access_token returns 401

Requires the stack to be running:
    docker-compose up -d
    uv run pytest tests/test_card16.py -v
"""

import hashlib
import uuid as uuid_mod

import psycopg2
import pytest
import requests

BACKEND_URL = "http://localhost:8000"
DB_DSN = "host=localhost port=5432 dbname=hypertube user=hypertube password=hypertube_dev_pass"

TOKEN_URL = f"{BACKEND_URL}/oauth/token"
USERS_URL = f"{BACKEND_URL}/users"
MOVIES_URL = f"{BACKEND_URL}/movies"
COMMENTS_URL = f"{BACKEND_URL}/comments"

REGISTER_URL = f"{BACKEND_URL}/api/auth/register"
LOGIN_URL = f"{BACKEND_URL}/api/auth/login"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


def _create_oauth_client(name: str | None = None) -> tuple[str, str]:
    """Insert an OAuth2 client in the DB. Returns (client_id, client_secret)."""
    if name is None:
        name = f"test_client_{_uid()}"
    client_id = f"client_{_uid()}"
    client_secret = f"secret_{_uid()}"
    secret_hash = hashlib.sha256(client_secret.encode()).hexdigest()

    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO oauth2_clients (client_id, client_secret_hash, name) "
                "VALUES (%s, %s, %s)",
                (client_id, secret_hash, name),
            )
        conn.commit()
    finally:
        conn.close()

    return client_id, client_secret


def _register_and_login() -> tuple[str, str, str, str]:
    """Register a fresh user. Returns (user_id, username, password, jwt_token)."""
    s = _uid()
    email = f"test16_{s}@example.com"
    username = f"u16_{s}"
    password = f"Secure1_{s}"

    r = requests.post(
        REGISTER_URL,
        files={
            "email": (None, email),
            "username": (None, username),
            "first_name": (None, "Test"),
            "last_name": (None, "User"),
            "password": (None, password),
        },
    )
    assert r.status_code == 201, f"Register failed: {r.text}"

    r2 = requests.post(LOGIN_URL, json={"username": username, "password": password})
    assert r2.status_code == 200, f"Login failed: {r2.text}"
    jwt_token = r2.json()["token"]

    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            user_id = str(cur.fetchone()[0])
    finally:
        conn.close()

    return user_id, username, password, jwt_token


def _get_token(client_id: str, client_secret: str) -> str:
    """Get a client_credentials access token."""
    resp = requests.post(
        TOKEN_URL,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    assert resp.status_code == 200, f"Token request failed: {resp.text}"
    return resp.json()["access_token"]


def _get_user_token(client_id: str, client_secret: str, username: str, password: str) -> str:
    """Get a password-grant access token (user-bound)."""
    resp = requests.post(
        TOKEN_URL,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "password",
            "username": username,
            "password": password,
        },
    )
    assert resp.status_code == 200, f"Password grant failed: {resp.text}"
    return resp.json()["access_token"]


def _seed_movie(jwt_token: str) -> str:
    """Seed a movie and return its imdb_id."""
    movie_id = f"test16_{_uid()}"
    requests.get(
        f"{BACKEND_URL}/api/movies/{movie_id}?title=Test16Movie",
        headers={"Authorization": f"Bearer {jwt_token}"},
        timeout=30,
    )
    return movie_id


def _get_movie_uuid(imdb_id: str) -> str | None:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM movies WHERE imdb_id = %s", (imdb_id,))
            row = cur.fetchone()
            return str(row[0]) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TEST 1: POST /oauth/token returns access_token
# ---------------------------------------------------------------------------

def test_oauth_token_returns_access_token():
    """POST /oauth/token with valid credentials returns access_token."""
    client_id, client_secret = _create_oauth_client()

    resp = requests.post(
        TOKEN_URL,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "access_token" in data, f"Response must have access_token: {data}"
    assert data.get("token_type") == "Bearer", f"token_type must be Bearer: {data}"
    assert "expires_in" in data, f"Response must have expires_in: {data}"
    assert data["expires_in"] == 3600, f"expires_in must be 3600: {data}"


# ---------------------------------------------------------------------------
# TEST 2: GET /users without token returns 401
# ---------------------------------------------------------------------------

def test_get_users_without_token_returns_401():
    """GET /users without Bearer token returns 401."""
    resp = requests.get(USERS_URL)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# TEST 3: GET /users with valid token returns array
# ---------------------------------------------------------------------------

def test_get_users_with_valid_token_returns_array():
    """GET /users with valid Bearer token returns array of users."""
    client_id, client_secret = _create_oauth_client()
    access_token = _get_token(client_id, client_secret)

    resp = requests.get(USERS_URL, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list), f"Response must be a list, got: {type(data)}"
    if len(data) > 0:
        assert "id" in data[0], f"Each user must have 'id': {data[0]}"
        assert "username" in data[0], f"Each user must have 'username': {data[0]}"


# ---------------------------------------------------------------------------
# TEST 4: GET /movies returns list with id and name
# ---------------------------------------------------------------------------

def test_get_movies_returns_list():
    """GET /movies returns list with id and name."""
    _, _, _, jwt_token = _register_and_login()
    _seed_movie(jwt_token)

    client_id, client_secret = _create_oauth_client()
    access_token = _get_token(client_id, client_secret)

    resp = requests.get(MOVIES_URL, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list), f"Response must be a list, got: {type(data)}"
    assert len(data) >= 1, "Must return at least 1 movie"
    assert "id" in data[0], f"Each movie must have 'id': {data[0]}"
    assert "name" in data[0], f"Each movie must have 'name': {data[0]}"


# ---------------------------------------------------------------------------
# TEST 5: GET /movies/:id returns mandatory fields
# ---------------------------------------------------------------------------

def test_get_movie_by_id_returns_required_fields():
    """GET /movies/:id returns all mandatory fields."""
    _, _, _, jwt_token = _register_and_login()
    imdb_id = _seed_movie(jwt_token)
    movie_uuid = _get_movie_uuid(imdb_id)
    if movie_uuid is None:
        pytest.skip("Movie not in DB after seeding")

    client_id, client_secret = _create_oauth_client()
    access_token = _get_token(client_id, client_secret)

    resp = requests.get(
        f"{MOVIES_URL}/{movie_uuid}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "id" in data, f"Missing 'id': {data}"
    assert "name" in data, f"Missing 'name': {data}"
    assert "imdb_rating" in data, f"Missing 'imdb_rating': {data}"
    assert "year" in data, f"Missing 'year': {data}"
    assert "available_subtitles" in data, f"Missing 'available_subtitles': {data}"
    assert "comments_count" in data, f"Missing 'comments_count': {data}"


# ---------------------------------------------------------------------------
# TEST 6: POST /comments creates comment and returns 201
# ---------------------------------------------------------------------------

def test_post_comment_creates_and_returns_201():
    """POST /comments creates comment and returns 201."""
    _, username, password, jwt_token = _register_and_login()
    imdb_id = _seed_movie(jwt_token)
    movie_uuid = _get_movie_uuid(imdb_id)
    if movie_uuid is None:
        pytest.skip("Movie not in DB after seeding")

    client_id, client_secret = _create_oauth_client()
    access_token = _get_user_token(client_id, client_secret, username, password)

    resp = requests.post(
        COMMENTS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"movie_id": movie_uuid, "content": "Test comment from OAuth2 API"},
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "id" in data, f"Response must have comment id: {data}"


# ---------------------------------------------------------------------------
# TEST 7: DELETE /comments/:id from another user returns 403
# ---------------------------------------------------------------------------

def test_delete_comment_from_another_user_returns_403():
    """DELETE /comments/:id from another user returns 403."""
    _, username_a, password_a, jwt_a = _register_and_login()
    _, username_b, password_b, _ = _register_and_login()
    _, _, _, jwt_seed = _register_and_login()
    imdb_id = _seed_movie(jwt_seed)
    movie_uuid = _get_movie_uuid(imdb_id)
    if movie_uuid is None:
        pytest.skip("Movie not in DB after seeding")

    client_id, client_secret = _create_oauth_client()

    # User A creates a comment
    token_a = _get_user_token(client_id, client_secret, username_a, password_a)
    create_resp = requests.post(
        COMMENTS_URL,
        headers={"Authorization": f"Bearer {token_a}"},
        json={"movie_id": movie_uuid, "content": "Comment by user A"},
    )
    assert create_resp.status_code == 201, f"Create comment failed: {create_resp.text}"
    comment_id = create_resp.json()["id"]

    # User B tries to delete user A's comment
    token_b = _get_user_token(client_id, client_secret, username_b, password_b)
    del_resp = requests.delete(
        f"{COMMENTS_URL}/{comment_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert del_resp.status_code == 403, (
        f"Expected 403, got {del_resp.status_code}: {del_resp.text}"
    )


# ---------------------------------------------------------------------------
# TEST 8: GET nonexistent route returns 404
# ---------------------------------------------------------------------------

def test_nonexistent_route_returns_404():
    """GET /nonexistent-route-16xyz returns 404."""
    resp = requests.get(f"{BACKEND_URL}/nonexistent-route-16xyz")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


# ---------------------------------------------------------------------------
# TEST 9: PUT /users returns 405
# ---------------------------------------------------------------------------

def test_put_users_returns_405():
    """PUT /users returns 405 Method Not Allowed."""
    client_id, client_secret = _create_oauth_client()
    access_token = _get_token(client_id, client_secret)

    resp = requests.put(
        USERS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={},
    )
    assert resp.status_code == 405, f"Expected 405, got {resp.status_code}"


# ---------------------------------------------------------------------------
# TEST 10: Expired token returns 401
# ---------------------------------------------------------------------------

def test_expired_token_returns_401():
    """Expired access_token returns 401."""
    client_id, client_secret = _create_oauth_client()
    access_token = _get_token(client_id, client_secret)

    # Expire the token in the DB
    token_hash = hashlib.sha256(access_token.encode()).hexdigest()
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE oauth2_tokens SET expires_at = NOW() - INTERVAL '1 second' "
                "WHERE token_hash = %s",
                (token_hash,),
            )
        conn.commit()
    finally:
        conn.close()

    resp = requests.get(USERS_URL, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

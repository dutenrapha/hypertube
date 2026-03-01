"""
CARD 02 — Schema do Banco de Dados
100% coverage of acceptance criteria.

Requires the stack to be running:
    docker compose up --build -d
    uv run pytest tests/test_card02.py -v
"""

import os
import subprocess
import time

import psycopg2
import psycopg2.errors
import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_TABLES = {
    "users",
    "oauth_accounts",
    "password_reset_tokens",
    "movies",
    "watched_movies",
    "comments",
    "oauth2_clients",
    "oauth2_tokens",
}

BACKEND_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env() -> dict:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    cfg = {}
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


def _connect_db() -> psycopg2.extensions.connection:
    deadline = time.time() + 30
    while True:
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            conn.autocommit = False
            return conn
        except psycopg2.OperationalError as e:
            if time.time() > deadline:
                pytest.fail(f"Could not connect to PostgreSQL: {e}")
            time.sleep(1)


def _wait_backend_healthy(timeout: int = 30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{BACKEND_URL}/health", timeout=5).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail("Backend did not become healthy in time")


# ---------------------------------------------------------------------------
# TEST: sqlx migrate run termina sem erro
# ---------------------------------------------------------------------------

def test_migrations_ran_backend_healthy():
    """Backend /health returns 200 — proves migrations ran successfully on startup."""
    resp = requests.get(f"{BACKEND_URL}/health", timeout=10)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TEST: todas as 8 tabelas existem após migration
# ---------------------------------------------------------------------------

def test_all_8_tables_exist():
    """All 8 required tables must be present in the public schema."""
    conn = _connect_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """
    )
    existing = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.close()

    missing = EXPECTED_TABLES - existing
    assert not missing, f"Missing tables after migration: {missing}"


# ---------------------------------------------------------------------------
# TEST: coluna password_hash NÃO aceita insert de texto plain
# ---------------------------------------------------------------------------

def test_password_hash_rejects_plain_text():
    """CHECK constraint must reject a plain-text password_hash."""
    conn = _connect_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO users (email, username, first_name, last_name, password_hash)
            VALUES ('chk_plain@example.com', 'chk_plain', 'A', 'B', 'plaintext')
            """
        )
        conn.rollback()
        pytest.fail("Expected CheckViolation for plain text password_hash")
    except psycopg2.errors.CheckViolation:
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def test_password_hash_accepts_bcrypt():
    """A valid bcrypt hash (starting with '$2b$') must be accepted."""
    # A bcrypt hash is exactly 60 chars, starts with $2b$12$...
    bcrypt_hash = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/lewdbcqgivjlmabvi"
    conn = _connect_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO users (email, username, first_name, last_name, password_hash)
            VALUES ('chk_bcrypt@example.com', 'chk_bcrypt', 'A', 'B', %s)
            RETURNING id
            """,
            (bcrypt_hash,),
        )
        assert cur.fetchone() is not None
        conn.rollback()
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# TEST: foreign keys estão configuradas
# ---------------------------------------------------------------------------

def test_foreign_key_enforced_on_comments():
    """Inserting a comment with a non-existent movie_id must raise ForeignKeyViolation."""
    conn = _connect_db()
    cur = conn.cursor()
    try:
        # Create a real user first
        cur.execute(
            """
            INSERT INTO users (email, username, first_name, last_name)
            VALUES ('fk_test@example.com', 'fk_test_user', 'FK', 'Test')
            RETURNING id
            """
        )
        user_id = cur.fetchone()[0]

        # Try to insert a comment with a bogus movie_id
        cur.execute(
            """
            INSERT INTO comments (user_id, movie_id, content)
            VALUES (%s, '00000000-0000-0000-0000-000000000000', 'bad comment')
            """,
            (user_id,),
        )
        conn.rollback()
        pytest.fail("Expected ForeignKeyViolation for invalid movie_id")
    except psycopg2.errors.ForeignKeyViolation:
        conn.rollback()
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# TEST: duas execuções de migrate run são idempotentes
# ---------------------------------------------------------------------------

def test_migrations_are_idempotent():
    """Restarting the backend (re-running migrations) must not fail."""
    result = subprocess.run(
        ["docker-compose", "restart", "backend"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"docker-compose restart failed:\n{result.stderr}"
    _wait_backend_healthy()


# ---------------------------------------------------------------------------
# TEST: rollback da migration funciona (migrate revert)
# ---------------------------------------------------------------------------

def test_migration_rollback_and_rerun():
    """
    Simulates 'migrate revert' + 'migrate run':
    Drop all app tables + migration history → restart backend → verify recreated.
    """
    conn = _connect_db()
    cur = conn.cursor()

    # Drop all tables in reverse dependency order (simulates migrate revert)
    cur.execute(
        """
        DROP TABLE IF EXISTS oauth2_tokens, oauth2_clients, comments,
                             watched_movies, movies, password_reset_tokens,
                             oauth_accounts, users, _sqlx_migrations CASCADE;
        """
    )
    conn.commit()
    cur.close()
    conn.close()

    # Restart backend → sqlx sees empty DB and re-runs all migrations
    result = subprocess.run(
        ["docker-compose", "restart", "backend"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"restart failed:\n{result.stderr}"
    _wait_backend_healthy(timeout=45)

    # Verify all tables were recreated
    conn2 = _connect_db()
    cur2 = conn2.cursor()
    cur2.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """
    )
    existing = {row[0] for row in cur2.fetchall()}
    cur2.close()
    conn2.close()

    missing = EXPECTED_TABLES - existing
    assert not missing, f"Tables not recreated after rollback + rerun: {missing}"

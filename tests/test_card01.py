"""
CARD 01 — Infraestrutura Docker
100% coverage of acceptance criteria.

Run after `docker-compose up --build -d`:
    uv run pytest tests/test_card01.py -v
"""

import os
import subprocess
import time
import pytest
import requests

BACKEND_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:3000"
ARIA2C_URL = "http://localhost:6800/jsonrpc"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Infrastructure tests
# ---------------------------------------------------------------------------

def test_docker_compose_build_exits_zero():
    """docker-compose up --build -d terminates with exit code 0."""
    # Bring down first to avoid docker-compose v1 ContainerConfig recreation bug
    subprocess.run(
        ["docker-compose", "down"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = subprocess.run(
        ["docker-compose", "up", "--build", "-d"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"docker-compose up --build -d failed:\n{result.stderr}"
    )


def test_backend_health_returns_200():
    """GET /health on the backend returns HTTP 200."""
    resp = requests.get(f"{BACKEND_URL}/health", timeout=10)
    assert resp.status_code == 200


def test_frontend_returns_html():
    """Frontend dev server returns an HTML page."""
    # Frontend (Vite) may need a few seconds to be ready after docker-compose up -d
    last_err = None
    for _ in range(12):  # up to ~12s
        try:
            resp = requests.get(FRONTEND_URL, timeout=5)
            assert resp.status_code == 200
            assert "text/html" in resp.headers.get("content-type", "")
            return
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
            last_err = e
            time.sleep(1)
    raise AssertionError(f"Frontend never became ready: {last_err}")


def test_aria2c_rpc_responds():
    """aria2c RPC endpoint responds to a getVersion call."""
    payload = {
        "jsonrpc": "2.0",
        "id": "test",
        "method": "aria2.getVersion",
        "params": [],
    }
    resp = requests.post(ARIA2C_URL, json=payload, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    assert "version" in data["result"]


# ---------------------------------------------------------------------------
# .env / .gitignore tests
# ---------------------------------------------------------------------------

def test_env_file_exists():
    """.env file exists in project root."""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    assert os.path.isfile(env_path), ".env not found"


def test_env_listed_in_gitignore():
    """.env is listed in .gitignore."""
    gitignore_path = os.path.join(PROJECT_ROOT, ".gitignore")
    assert os.path.isfile(gitignore_path), ".gitignore not found"
    with open(gitignore_path) as f:
        lines = [line.strip() for line in f.readlines()]
    assert ".env" in lines, ".env not found in .gitignore"


def test_env_example_exists():
    """.env.example exists in project root."""
    path = os.path.join(PROJECT_ROOT, ".env.example")
    assert os.path.isfile(path), ".env.example not found"


def test_env_example_contains_all_required_keys():
    """.env.example documents all required keys."""
    required_keys = [
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
        "JWT_SECRET",
        "ARIA2_RPC_SECRET",
        "OAUTH_42_CLIENT_ID",
        "OAUTH_42_CLIENT_SECRET",
        "OAUTH_42_REDIRECT_URI",
        "OAUTH_GOOGLE_CLIENT_ID",
        "OAUTH_GOOGLE_CLIENT_SECRET",
        "OAUTH_GOOGLE_REDIRECT_URI",
    ]
    path = os.path.join(PROJECT_ROOT, ".env.example")
    with open(path) as f:
        content = f.read()
    for key in required_keys:
        assert key in content, f"Key {key!r} missing from .env.example"


def test_env_was_never_committed():
    """.env has never appeared in any git commit."""
    result = subprocess.run(
        ["git", "log", "--all", "--", ".env"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", (
        ".env was found in git history — this is a critical violation!"
    )

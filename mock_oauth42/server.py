#!/usr/bin/env python3
"""
Mock 42 OAuth server for testing.

Simulates the endpoints used during the OAuth2 flow:
  POST /oauth/token  — code exchange
  GET  /v2/me        — user info
  GET  /health       — healthcheck
"""
import hashlib
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockOAuth42Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress access logs

    # ------------------------------------------------------------------
    def do_POST(self):
        if self.path == "/oauth/token":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            params = dict(urllib.parse.parse_qsl(body))
            code = params.get("code", "")

            if code.startswith("invalid"):
                self._json(401, {"error": "invalid_grant",
                                 "message": "The provided authorization grant is invalid"})
            else:
                # Echo the code back as the access_token for determinism
                self._json(200, {
                    "access_token": code,
                    "token_type": "bearer",
                    "expires_in": 7200,
                    "scope": "public",
                    "created_at": 1700000000,
                })
        else:
            self._json(404, {"error": "not_found"})

    # ------------------------------------------------------------------
    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})

        elif self.path == "/v2/me":
            auth = self.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()

            # Deterministic numeric id from token
            user_id = int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % 1_000_000

            # Use the token itself as login/email-prefix so tests can predict the email
            login = token[:20] if len(token) >= 3 else f"u42_{user_id}"

            self._json(200, {
                "id": user_id,
                "email": f"{token}@mock42.test",
                "login": login,
                "first_name": "Test",
                "last_name": "User42",
                "image": {"link": "https://example.com/avatar.jpg"},
            })
        else:
            self._json(404, {"error": "not_found"})

    # ------------------------------------------------------------------
    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), MockOAuth42Handler)
    print("Mock 42 OAuth server running on :8080", flush=True)
    server.serve_forever()

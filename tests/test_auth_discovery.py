"""Check auth discovery with real dependencies in an isolated process."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("enabled", [False, True])
def test_oauth_routes_follow_auth_setting(enabled, tmp_path):
    # Some tests reload modules with mocked dependencies. A fresh process
    # exercises application construction and actual HTTP routes without caches.
    script = """
import sys
from fastapi.testclient import TestClient
from pydantic import SecretStr
from keycloak_mcp_server.src.settings import settings

settings.ENABLE_AUTH = sys.argv[1] == "True"
settings.KEYCLOAK_SERVER_URL = "https://keycloak.example/"
settings.KEYCLOAK_REALM = "employees"
settings.KEYCLOAK_OAUTH_CLIENT_ID = "keycloak-mcp"
settings.KEYCLOAK_OAUTH_CLIENT_SECRET = None
settings.KEYCLOAK_OAUTH_SIGNING_KEY = SecretStr("a-test-signing-key-of-at-least-32-characters")
settings.KEYCLOAK_OAUTH_STORAGE_DIR = sys.argv[2]
settings.KEYCLOAK_DEV_ACCESS_TOKEN = None

from keycloak_mcp_server.src.api import app

with TestClient(app) as client:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["authentication"] == ("oauth" if settings.ENABLE_AUTH else "disabled")
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
    ):
        response = client.get(path)
        assert response.status_code == (200 if settings.ENABLE_AUTH else 404)
        if settings.ENABLE_AUTH and path.endswith("authorization-server"):
            assert response.json()["registration_endpoint"].endswith("/register")
        if not settings.ENABLE_AUTH:
            assert "registration_endpoint" not in response.text
    # Old, placeholder-token controllers are never exposed in either mode.
    assert client.post("/auth/register", json={}).status_code == 404
    registration = client.post("/register", json={
        "client_name": "Codex",
        "redirect_uris": ["http://127.0.0.1:8765/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    assert registration.status_code == (201 if settings.ENABLE_AUTH else 404)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(enabled), str(tmp_path / "tokens")],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

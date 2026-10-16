"""Check auth discovery with real dependencies in an isolated process."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("enabled", [False, True])
def test_oauth_routes_follow_auth_setting(enabled, tmp_path):
    script = """
import sys
from fastapi.testclient import TestClient
from keycloak_mcp_server.src.settings import settings

settings.ENABLE_AUTH = sys.argv[1] == "True"
settings.KEYCLOAK_SERVER_URL = "https://keycloak.example/"
settings.KEYCLOAK_REALM = "employees"
settings.KEYCLOAK_OAUTH_CLIENT_ID = "keycloak-mcp"
settings.KEYCLOAK_DEV_ACCESS_TOKEN = None

from keycloak_mcp_server.src.api import app

with TestClient(app) as client:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["authentication"] == ("keycloak" if settings.ENABLE_AUTH else "disabled")
    protected = client.get("/.well-known/oauth-protected-resource/mcp")
    assert protected.status_code == (200 if settings.ENABLE_AUTH else 404)
    # No DCR endpoint: the server is a resource server only.
    if not settings.ENABLE_AUTH:
        assert "registration_endpoint" not in protected.text
"""
    subprocess.run(
        [sys.executable, "-c", script, str(enabled)],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

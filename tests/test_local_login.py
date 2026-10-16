"""Exercise stdio-mode browser sign-in against a simulated Keycloak and browser."""

import asyncio
import base64
import hashlib
import json
import os
import socket
import stat
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from keycloak_mcp_server.src.keycloak_api import local_login as login_module
from keycloak_mcp_server.src.keycloak_api import service
from keycloak_mcp_server.src.keycloak_api.local_login import LocalLogin
from keycloak_mcp_server.src.settings import Settings
from keycloak_mcp_server.src.tools import keycloak_tools

REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def mock_imports():
    """Override the conftest fixture: these tests use real dependencies."""
    yield


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def keycloak(monkeypatch, tmp_path):
    """Simulate Keycloak's token endpoint and a browser that signs in."""
    config = Settings(
        MCP_TRANSPORT_PROTOCOL="stdio",
        KEYCLOAK_SERVER_URL="https://keycloak.example/",
        KEYCLOAK_REALM="myrealm",
        KEYCLOAK_OAUTH_CLIENT_ID="keycloak-mcp",
        KEYCLOAK_LOGIN_PORT=free_port(),
        KEYCLOAK_TOKEN_CACHE_DIR=str(tmp_path / "cache"),
    )
    state = {"token_requests": [], "browser_urls": [], "refresh_ok": True, "n": 0}

    def token_endpoint(request):
        assert request.url.path == "/realms/myrealm/protocol/openid-connect/token"
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        state["token_requests"].append(form)
        if form["grant_type"] == "refresh_token" and not state["refresh_ok"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        state["n"] += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{state['n']}",
                "refresh_token": f"refresh-{state['n']}",
                "expires_in": 300,
            },
        )

    def keycloak_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(token_endpoint)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(login_module.httpx, "AsyncClient", keycloak_client)

    def browser(url, callback=None):
        """Open the sign-in URL: Keycloak redirects back with a code."""
        state["browser_urls"].append(url)
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        query = (
            callback(params)
            if callback
            else {
                "code": "the-code",
                "state": params["state"],
            }
        )

        # webbrowser.open runs in a worker thread, so follow the redirect with
        # a blocking client; the callback server runs on the free event loop.
        with httpx.Client() as client:
            state.setdefault("callback_responses", []).append(
                client.get(params["redirect_uri"], params=query)
            )
        return True

    state["browser"] = browser
    monkeypatch.setattr(login_module.webbrowser, "open", lambda url: browser(url))
    return config, state


@pytest.mark.asyncio
async def test_browser_sign_in_uses_pkce_and_caches_tokens_privately(keycloak):
    config, state = keycloak
    login = LocalLogin(config)
    assert await login.access_token() == "access-1"

    params = parse_qs(urlparse(state["browser_urls"][0]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["client_id"] == ["keycloak-mcp"]
    assert params["redirect_uri"] == [
        f"http://127.0.0.1:{config.KEYCLOAK_LOGIN_PORT}/callback"
    ]
    exchange = state["token_requests"][0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "the-code"
    assert "client_secret" not in exchange
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(exchange["code_verifier"].encode()).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert params["code_challenge"] == [challenge]
    assert state["callback_responses"][0].status_code == 200

    cache = Path(config.KEYCLOAK_TOKEN_CACHE_DIR).expanduser() / "tokens.json"
    assert stat.S_IMODE(os.stat(cache).st_mode) == 0o600
    assert "refresh-1" in cache.read_text()

    # A new process reads the cache: no browser, no Keycloak request.
    assert await LocalLogin(config).access_token() == "access-1"
    assert len(state["browser_urls"]) == 1
    assert len(state["token_requests"]) == 1


@pytest.mark.asyncio
async def test_expired_token_is_refreshed_without_a_browser(keycloak):
    config, state = keycloak
    login = LocalLogin(config)
    await login.access_token()
    login.invalidate_access_token()
    assert await login.access_token() == "access-2"
    assert state["token_requests"][1] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-1",
        "client_id": "keycloak-mcp",
    }
    assert len(state["browser_urls"]) == 1
    cached = json.loads(
        (Path(config.KEYCLOAK_TOKEN_CACHE_DIR) / "tokens.json").read_text()
    )
    assert next(iter(cached.values()))["refresh_token"] == "refresh-2"


@pytest.mark.asyncio
async def test_rejected_refresh_falls_back_to_browser_sign_in(keycloak):
    config, state = keycloak
    login = LocalLogin(config)
    await login.access_token()
    login.invalidate_access_token()
    state["refresh_ok"] = False
    assert await login.access_token() == "access-2"
    assert len(state["browser_urls"]) == 2


@pytest.mark.asyncio
async def test_callback_with_wrong_state_is_rejected(keycloak, monkeypatch):
    config, state = keycloak
    attempts = []

    def forged_then_real(url):
        attempts.append(url)
        if len(attempts) == 1:
            # A forged redirect first; then the real one from Keycloak.
            state["browser"](url, lambda p: {"code": "evil", "state": "forged"})
        return state["browser"](url)

    monkeypatch.setattr(login_module.webbrowser, "open", forged_then_real)
    assert await LocalLogin(config).access_token() == "access-1"
    statuses = sorted(r.status_code for r in state["callback_responses"])
    assert statuses == [200, 400]
    assert [r["code"] for r in state["token_requests"]] == ["the-code"]


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_sign_in(keycloak):
    config, state = keycloak
    login = LocalLogin(config)
    tokens = await asyncio.gather(*(login.access_token() for _ in range(5)))
    assert set(tokens) == {"access-1"}
    assert len(state["browser_urls"]) == 1


@pytest.mark.asyncio
async def test_stdio_tools_call_admin_api_with_the_signed_in_token(monkeypatch):
    config = Settings(
        MCP_TRANSPORT_PROTOCOL="stdio",
        ENABLE_AUTH=True,  # Ignored in stdio mode: there is no HTTP sign-in.
        KEYCLOAK_SERVER_URL="https://keycloak.example/",
        KEYCLOAK_REALM="myrealm",
        KEYCLOAK_OAUTH_CLIENT_ID="keycloak-mcp",
        KEYCLOAK_DEV_ACCESS_TOKEN="DO_NOT_USE",
    )
    monkeypatch.setattr(service, "settings", config)
    invalidated = []

    class FakeLogin:
        async def access_token(self):
            return "signed-in-user-token"

        def invalidate_access_token(self):
            invalidated.append(True)

    monkeypatch.setattr(service, "_local_login", FakeLogin())
    seen = []

    def admin_api(request):
        seen.append(request.headers["authorization"])
        if len(seen) == 2:
            return httpx.Response(401, json={})
        return httpx.Response(200, json=[{"id": "c-1", "clientId": "portal"}])

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(admin_api)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr("keycloak.connection.httpx.AsyncClient", client_factory)
    result = await keycloak_tools.list_clients()
    assert result["items"][0]["clientId"] == "portal"
    assert seen == ["Bearer signed-in-user-token"]
    with pytest.raises(ToolError, match="Call the tool again"):
        await keycloak_tools.list_clients()
    assert invalidated == [True]


@pytest.mark.asyncio
async def test_stdio_entrypoint_speaks_mcp_without_oauth_proxy_settings(tmp_path):
    # Launch the real CLI the way `claude mcp add ... -- uvx keycloak-mcp-server
    # --stdio` would. stdout must carry only the protocol: logs go to stderr.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "KEYCLOAK_SERVER_URL": "https://keycloak.example",
        "KEYCLOAK_REALM": "myrealm",
        "KEYCLOAK_OAUTH_CLIENT_ID": "keycloak-mcp",
    }
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "keycloak_mcp_server.src.main", "--stdio"],
        env=env,
        cwd=str(tmp_path),  # No .env here, unlike the repository root.
    )
    started = time.monotonic()
    async with Client(transport) as client:
        tools = await client.list_tools()
    assert len(tools) == len(keycloak_tools.KEYCLOAK_TOOLS)
    assert time.monotonic() - started < 60

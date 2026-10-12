"""Exercise browser OAuth and authenticated MCP calls against a simulated Keycloak."""

import asyncio
import base64
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.integrations.httpx_client._compat import httpx2 as oauth_http
from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from starlette.responses import JSONResponse

from keycloak_mcp_server.src import api as api_module
from keycloak_mcp_server.src import mcp as mcp_module
from keycloak_mcp_server.src.keycloak_api import auth as auth_module
from keycloak_mcp_server.src.keycloak_api import service
from keycloak_mcp_server.src.mcp import KeycloakMCPServer
from keycloak_mcp_server.src.settings import Settings
from keycloak_mcp_server.src.tools import keycloak_tools


@pytest.fixture(autouse=True)
def mock_imports():
    """Use actual installed dependencies for OAuth/MCP integration."""
    yield


@pytest.fixture(params=[False, True], ids=["public-pkce", "confidential-pkce"])
def oauth_env(request, tmp_path, monkeypatch):
    use_client_secret = request.param
    config = Settings(
        ENABLE_AUTH=True,
        KEYCLOAK_SERVER_URL="https://keycloak.example/",
        KEYCLOAK_REALM="employees",
        KEYCLOAK_OAUTH_CLIENT_ID="keycloak-mcp",
        KEYCLOAK_OAUTH_CLIENT_SECRET=(
            "test-confidential-client-secret" if use_client_secret else None
        ),
        KEYCLOAK_OAUTH_SIGNING_KEY="test-signing-key-with-at-least-32-characters",
        # Pinned: a developer .env must not change what the minted tokens need.
        KEYCLOAK_OAUTH_AUDIENCE="realm-management",
        KEYCLOAK_OAUTH_STORAGE_DIR=str(tmp_path / "tokens"),
        KEYCLOAK_DEV_ACCESS_TOKEN=None,
        MCP_HOST_ENDPOINT="http://localhost:5001",
    )
    # Some tests reload modules while their imports are mocked.
    monkeypatch.setattr(api_module, "FastAPI", FastAPI)
    monkeypatch.setattr(api_module, "JSONResponse", JSONResponse)
    monkeypatch.setattr(api_module, "KeycloakMCPServer", KeycloakMCPServer)
    monkeypatch.setattr(
        api_module, "create_keycloak_auth", auth_module.create_keycloak_auth
    )
    monkeypatch.setattr(mcp_module, "FastMCP", FastMCP)
    monkeypatch.setattr(mcp_module, "settings", config)
    monkeypatch.setattr(service, "settings", config)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk = JsonWebKey.import_key(public, {"kid": "test-key"}).as_dict()
    jwt = JsonWebToken(["RS256"])
    issued = {}
    upstream_requests = []

    def mint(user, **changes):
        claims = {
            "iss": "https://keycloak.example/realms/employees",
            "aud": "realm-management",
            "azp": "keycloak-mcp",
            "typ": "Bearer",
            "sub": user,
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "scope": "openid profile email",
        }
        claims.update(changes)
        token = jwt.encode(
            {"alg": "RS256", "kid": "test-key"}, claims, private
        ).decode()
        issued[token] = user
        return token

    def handle(request):
        upstream_requests.append(request)
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            if use_client_secret:
                assert (
                    request.headers["authorization"]
                    == "Basic "
                    + base64.b64encode(
                        b"keycloak-mcp:test-confidential-client-secret"
                    ).decode()
                )
            else:
                assert "authorization" not in request.headers
                assert form["client_id"] == ["keycloak-mcp"]
                assert "client_secret" not in form
            assert form["grant_type"][0] in {"authorization_code", "refresh_token"}
            if form["grant_type"] == ["authorization_code"]:
                assert len(form["code_verifier"][0]) >= 43
                assert form["redirect_uri"] == ["http://localhost:5001/auth/callback"]
                user = form["code"][0].removeprefix("code-")
            else:
                user = form["refresh_token"][0].removeprefix("refresh-")
            return httpx.Response(
                200,
                json={
                    "access_token": mint(user),
                    "refresh_token": "refresh-" + user,
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "refresh_expires_in": 1800,
                    "scope": "openid profile email",
                },
            )
        assert request.method == "GET"
        upstream_token = request.headers["authorization"].removeprefix("Bearer ")
        assert upstream_token in issued, "MCP tokens must never reach the Admin API"
        user = issued[upstream_token]
        if request.url.path == "/admin/realms/employees/users":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "user-alice",
                        "username": "alice",
                        "enabled": True,
                        "credentials": [{"secretData": "must-not-be-returned"}],
                    }
                ],
            )
        if request.url.path == "/admin/realms/employees/users/user-alice":
            if user == "bob":
                return httpx.Response(403, json={"error": "private-upstream-detail"})
            return httpx.Response(200, json={"id": "user-alice", "username": "alice"})
        raise AssertionError(f"Unexpected upstream route: {request.url.path}")

    transport = httpx.MockTransport(handle)
    original_client = httpx.AsyncClient

    class NetworkClient(original_client):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", transport)
            original_client.__init__(self, *args, **kwargs)

    # The Admin API is called through python-keycloak, which uses httpx.
    monkeypatch.setattr(httpx, "AsyncClient", NetworkClient)

    # Authlib uses httpx2 when installed, with legacy httpx as its fallback.
    # Exercise the actual OAuth client in both dependency configurations.
    if oauth_http is not httpx:

        def oauth_handle(request):
            response = handle(request)
            return oauth_http.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        original_oauth_client = oauth_http.AsyncClient
        oauth_transport = oauth_http.MockTransport(oauth_handle)

        class OAuthNetworkClient(original_oauth_client):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("transport", oauth_transport)
                original_oauth_client.__init__(self, *args, **kwargs)

        monkeypatch.setattr(oauth_http, "AsyncClient", OAuthNetworkClient)

    app = api_module.create_app(config)

    def browser():
        return original_client(
            transport=httpx.ASGITransport(app), base_url="http://localhost:5001"
        )

    def mcp_client(token):
        # fastmcp 4's client uses httpx2.
        def factory(**kwargs):
            return original_oauth_client(
                transport=oauth_http.ASGITransport(app), **kwargs
            )

        return Client(
            StreamableHttpTransport(
                "http://localhost:5001/mcp",
                auth=token,
                httpx_client_factory=factory,
            )
        )

    return config, app, browser, mcp_client, mint, upstream_requests, issued


async def register_and_consent(browser, name):
    response = await browser.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": ["http://localhost:8765/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201, response.text
    registration = response.json()
    assert "test-confidential-client-secret" not in response.text
    verifier = "a" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    response = await browser.get(
        "/authorize",
        params={
            "client_id": registration["client_id"],
            "response_type": "code",
            "redirect_uri": "http://localhost:8765/callback",
            "state": "client-state",
            "scope": "openid profile email",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "http://localhost:5001/mcp",
        },
    )
    assert response.status_code in {302, 303}, response.text
    consent = response.headers["location"]
    response = await browser.get(consent)
    assert response.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    response = await browser.post(
        consent,
        data={
            "action": "approve",
            "csrf_token": csrf,
            "txn_id": parse_qs(urlparse(consent).query)["txn_id"][0],
        },
    )
    assert response.status_code == 302, response.text
    upstream_url = response.headers["location"]
    params = parse_qs(urlparse(upstream_url).query)
    assert params["client_id"] == ["keycloak-mcp"]
    assert params["redirect_uri"] == ["http://localhost:5001/auth/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert "resource" not in params
    return registration["client_id"], verifier, params["state"][0]


async def complete_login(browser, user):
    client_id, verifier, state = await register_and_consent(browser, user)
    response = await browser.get(
        "/auth/callback", params={"code": "code-" + user, "state": state}
    )
    assert response.status_code == 302, response.text
    callback = parse_qs(urlparse(response.headers["location"]).query)
    assert callback["state"] == ["client-state"]
    token_form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": callback["code"][0],
        "code_verifier": verifier,
        "redirect_uri": "http://localhost:8765/callback",
        "resource": "http://localhost:5001/mcp",
    }
    bad = await browser.post("/token", data={**token_form, "code_verifier": "b" * 64})
    assert bad.status_code in {400, 401}, bad.text
    assert bad.json()["error"] == "invalid_grant", bad.text
    response = await browser.post("/token", data=token_form)
    assert response.status_code == 200, response.text
    replay = await browser.post("/token", data=token_form)
    assert replay.status_code in {400, 401}
    assert replay.json()["error"] == "invalid_grant"
    return client_id, response.json()


@pytest.mark.asyncio
async def test_browser_oauth_and_two_users_call_real_mcp(oauth_env):
    config, app, browser, mcp_client, _, requests, issued = oauth_env
    async with app.router.lifespan_context(app):
        async with browser() as alice, browser() as bob:
            metadata = await alice.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200
            assert (
                metadata.json()["registration_endpoint"]
                == "http://localhost:5001/register"
            )
            protected = await alice.get("/.well-known/oauth-protected-resource/mcp")
            assert protected.status_code == 200
            assert protected.json()["resource"] == "http://localhost:5001/mcp"
            for client in (alice, bob):
                response = await client.post("/mcp", json={})
                assert response.status_code == 401
                assert "resource_metadata" in response.headers["www-authenticate"]
            alice_id, alice_tokens = await complete_login(alice, "alice")
            _, bob_tokens = await complete_login(bob, "bob")
            assert alice_tokens["access_token"] not in issued
            assert bob_tokens["access_token"] not in issued

            async def search_as(tokens):
                async with mcp_client(tokens["access_token"]) as client:
                    assert len(await client.list_tools()) == len(
                        keycloak_tools.KEYCLOAK_TOOLS
                    )
                    result = await client.call_tool(
                        "search_users", {"username": "alice", "exact": True}
                    )
                    assert result.data["items"][0]["username"] == "alice"
                    assert "must-not-be-returned" not in str(result)

            await asyncio.gather(search_as(alice_tokens), search_as(bob_tokens))
            calls = [r for r in requests if "/admin/" in r.url.path]
            assert {
                issued[r.headers["authorization"].removeprefix("Bearer ")]
                for r in calls
            } == {"alice", "bob"}
            async with mcp_client(bob_tokens["access_token"]) as client:
                with pytest.raises(ToolError, match="permission"):
                    await client.call_tool("get_user", {"user_id": "user-alice"})
            refreshed = await alice.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": alice_id,
                    "refresh_token": alice_tokens["refresh_token"],
                    "resource": "http://localhost:5001/mcp",
                },
            )
            assert refreshed.status_code == 200, refreshed.text
            assert refreshed.json()["access_token"] not in issued
            await search_as(refreshed.json())

            # Neither stored upstream access nor refresh tokens are plaintext on disk.
            stored = b"".join(
                p.read_bytes()
                for p in Path(config.KEYCLOAK_OAUTH_STORAGE_DIR).rglob("*")
                if p.is_file()
            )
            for token in issued:
                assert token.encode() not in stored
            assert b"refresh-alice" not in stored
            assert b"test-confidential-client-secret" not in stored
            raw_upstream = next(iter(issued))
            response = await alice.post(
                "/mcp", headers={"Authorization": "Bearer " + raw_upstream}, json={}
            )
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_oauth_callback_is_bound_to_consenting_browser(oauth_env):
    _, app, browser, _, _, requests, _ = oauth_env
    async with app.router.lifespan_context(app):
        async with browser() as owner, browser() as stranger:
            _, _, state = await register_and_consent(owner, "alice")
            response = await stranger.get(
                "/auth/callback", params={"code": "code-alice", "state": state}
            )
            assert response.status_code == 403
            assert not any(r.url.path.endswith("/token") for r in requests)
            response = await owner.get(
                "/auth/callback", params={"code": "code-alice", "state": state}
            )
            assert response.status_code == 302


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "unrelated-app"},
        {"iss": "https://evil.example/realms/employees"},
        {"exp": 1},
        {"exp": None},
        {"nbf": 9999999999},
        {"azp": "unrelated-client"},
        {"typ": "ID"},
        {"sub": ""},
    ],
)
async def test_upstream_tokens_require_claims_and_expected_identity(oauth_env, changes):
    _, app, _, _, mint, _, _ = oauth_env
    verifier = app.state.keycloak_auth._token_validator
    try:
        assert await verifier.verify_token(mint("alice", **changes)) is None
    finally:
        await app.state.keycloak_auth.aclose()


def exchange_app(config):
    """Rebuild the app with an RFC 8693 exchanger trusted, e.g. a gateway."""
    config.KEYCLOAK_TRUSTED_EXCHANGERS = ["mcp-context-forge"]
    return api_module.create_app(config)


def asgi_mcp_client(app, token):
    def factory(**kwargs):
        return oauth_http.AsyncClient(transport=oauth_http.ASGITransport(app), **kwargs)

    return Client(
        StreamableHttpTransport(
            "http://localhost:5001/mcp", auth=token, httpx_client_factory=factory
        )
    )


def exchanged(mint, user, **changes):
    """A token as Keycloak issues it after a gateway's on-behalf-of exchange."""
    claims = {
        "aud": ["mcp-context-forge", "realm-management"],
        "azp": "mcp-context-forge",
        "scope": "exchange-keycloak-mcp",  # No openid: only the exchange scope.
    }
    claims.update(changes)
    return mint(user, **claims)


@pytest.mark.asyncio
async def test_exchanged_token_from_trusted_gateway_calls_admin_api_as_user(
    oauth_env,
):
    config, _, _, _, mint, requests, issued = oauth_env
    app = exchange_app(config)
    assert isinstance(app.state.keycloak_auth, auth_module.KeycloakMultiAuth)
    token = exchanged(mint, "alice")
    async with app.router.lifespan_context(app):
        async with asgi_mcp_client(app, token) as client:
            result = await client.call_tool(
                "search_users", {"username": "alice", "exact": True}
            )
    assert result.data["items"][0]["username"] == "alice"
    admin_calls = [r for r in requests if "/admin/" in r.url.path]
    # The exchanged user token itself reaches the Admin API: still Alice.
    assert [r.headers["authorization"] for r in admin_calls] == ["Bearer " + token]
    assert issued[token] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"azp": "keycloak-mcp"},  # A plain sign-in token must not skip the proxy.
        {"azp": "unrelated-client"},
        {"aud": ["mcp-context-forge"]},  # Exchanged for some other audience.
        {"iss": "https://evil.example/realms/employees"},
        {"exp": 1},
        {"typ": "ID"},
        {"sub": ""},
    ],
)
async def test_exchange_path_rejects_tokens_outside_its_trust(oauth_env, changes):
    config, _, _, _, mint, requests, _ = oauth_env
    app = exchange_app(config)
    token = exchanged(mint, "alice", **changes)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:5001"
        ) as client:
            response = await client.post(
                "/mcp", headers={"Authorization": "Bearer " + token}, json={}
            )
    assert response.status_code == 401
    assert not [r for r in requests if "/admin/" in r.url.path]


@pytest.mark.asyncio
async def test_exchanged_tokens_are_rejected_unless_an_exchanger_is_trusted(
    oauth_env,
):
    _, app, browser, _, mint, _, _ = oauth_env
    assert isinstance(app.state.keycloak_auth, auth_module.KeycloakOAuthProxy)
    async with app.router.lifespan_context(app):
        async with browser() as client:
            response = await client.post(
                "/mcp",
                headers={"Authorization": "Bearer " + exchanged(mint, "alice")},
                json={},
            )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_browser_sign_in_still_works_with_exchange_enabled(oauth_env):
    config, _, _, _, _, _, _ = oauth_env
    app = exchange_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:5001"
        ) as browser:
            discovery = await browser.get("/.well-known/oauth-authorization-server")
            assert discovery.status_code == 200
            _, tokens = await complete_login(browser, "bob")
        async with asgi_mcp_client(app, tokens["access_token"]) as client:
            result = await client.call_tool(
                "search_users", {"username": "alice", "exact": True}
            )
    assert result.data["items"][0]["username"] == "alice"


@pytest.mark.parametrize(
    "exchangers, message",
    [
        (["keycloak-mcp"], "must not include KEYCLOAK_OAUTH_CLIENT_ID"),
        ([""], "must be client IDs"),
        ([" mcp-context-forge"], "must be client IDs"),
    ],
)
def test_trusted_exchangers_are_validated(oauth_env, exchangers, message):
    config = oauth_env[0]
    config.KEYCLOAK_TRUSTED_EXCHANGERS = exchangers
    with pytest.raises(ValueError, match=message):
        auth_module.create_keycloak_auth(config)


@pytest.mark.asyncio
async def test_oauth_mode_ignores_shared_test_tokens(oauth_env):
    _, app, _, _, _, requests, _ = oauth_env
    try:
        with service.keycloak_user_access_token("shared-test-token"):
            with pytest.raises(ToolError, match="Sign in"):
                await keycloak_tools.search_users(username="alice")
        assert requests == []
    finally:
        await app.state.keycloak_auth.aclose()

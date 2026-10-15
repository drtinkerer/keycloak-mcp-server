"""Exercise stateless Keycloak auth and MCP calls against a simulated Keycloak."""

import asyncio
import ssl
import time

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
    """Use actual installed dependencies for auth/MCP integration."""
    yield


@pytest.fixture
def oauth_env(tmp_path, monkeypatch):
    config = Settings(
        ENABLE_AUTH=True,
        KEYCLOAK_SERVER_URL="https://keycloak.example/",
        KEYCLOAK_REALM="employees",
        KEYCLOAK_OAUTH_CLIENT_ID="keycloak-mcp",
        KEYCLOAK_OAUTH_AUDIENCE="keycloak-mcp",
        KEYCLOAK_CA_BUNDLE=None,
        KEYCLOAK_DEV_ACCESS_TOKEN=None,
        MCP_HOST_ENDPOINT="http://localhost:5001",
    )
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
            "aud": ["keycloak-mcp", "realm-management", "account"],
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
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://keycloak.example/realms/employees",
                    "authorization_endpoint": "https://keycloak.example/realms/employees/protocol/openid-connect/auth",
                    "token_endpoint": "https://keycloak.example/realms/employees/protocol/openid-connect/token",
                    "jwks_uri": "https://keycloak.example/realms/employees/protocol/openid-connect/certs",
                },
            )
        assert request.method == "GET"
        upstream_token = request.headers["authorization"].removeprefix("Bearer ")
        assert upstream_token in issued
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

    monkeypatch.setattr(httpx, "AsyncClient", NetworkClient)

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
    else:
        original_oauth_client = oauth_http.AsyncClient

    app = api_module.create_app(config)

    def browser():
        return original_client(
            transport=httpx.ASGITransport(app), base_url="http://localhost:5001"
        )

    def mcp_client(token):
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


@pytest.mark.asyncio
async def test_protected_resource_metadata_points_to_keycloak(oauth_env):
    _, app, browser, _, _, _, _ = oauth_env
    async with app.router.lifespan_context(app):
        async with browser() as client:
            protected = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert protected.status_code == 200
            data = protected.json()
            assert data["resource"] == "http://localhost:5001/mcp"
            assert [s.rstrip("/") for s in data["authorization_servers"]] == [
                "https://keycloak.example/realms/employees"
            ]
            assert "openid" in data["scopes_supported"]
            # A resource server only: no proxy endpoints, nothing to store.
            for path in ("/authorize", "/token", "/register", "/auth/callback"):
                assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(oauth_env):
    _, app, browser, _, _, _, _ = oauth_env
    async with app.router.lifespan_context(app):
        async with browser() as client:
            response = await client.post("/mcp", json={})
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_keycloak_token_calls_admin_api(oauth_env):
    _, app, _, mcp_client, mint, requests, issued = oauth_env
    token = mint("alice")
    async with app.router.lifespan_context(app):
        async with mcp_client(token) as client:
            assert len(await client.list_tools()) == len(keycloak_tools.KEYCLOAK_TOOLS)
            result = await client.call_tool(
                "search_users", {"username": "alice", "exact": True}
            )
            assert result.data["items"][0]["username"] == "alice"
            assert "must-not-be-returned" not in str(result)
    admin_calls = [r for r in requests if "/admin/" in r.url.path]
    assert all(r.headers["authorization"] == f"Bearer {token}" for r in admin_calls)


@pytest.mark.asyncio
async def test_two_users_isolated_by_token(oauth_env):
    _, app, _, mcp_client, mint, requests, issued = oauth_env
    alice_token = mint("alice")
    bob_token = mint("bob")
    async with app.router.lifespan_context(app):
        async with mcp_client(alice_token) as alice, mcp_client(bob_token) as bob:
            await asyncio.gather(
                alice.call_tool("search_users", {"username": "alice", "exact": True}),
                bob.call_tool("search_users", {"username": "alice", "exact": True}),
            )
        async with mcp_client(bob_token) as client:
            with pytest.raises(ToolError, match="permission"):
                await client.call_tool("get_user", {"user_id": "user-alice"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "unrelated-app"},
        {"aud": ["realm-management", "account"]},
        {"iss": "https://evil.example/realms/employees"},
        {"exp": 1},
        {"nbf": int(time.time()) + 300},
        # Another client of the realm, even with this server's audience.
        {"azp": "mcp-clients"},
        {"azp": "mcp-context-forge"},
        {"typ": "ID"},
        {"sub": ""},
        {"scope": "profile email"},
    ],
)
async def test_invalid_tokens_are_rejected(oauth_env, changes):
    """Sign-in tokens must be this realm's access tokens for this client and audience."""
    _, app, browser, _, mint, _, _ = oauth_env
    token = mint("alice", **changes)
    async with app.router.lifespan_context(app):
        async with browser() as client:
            response = await client.post(
                "/mcp", headers={"Authorization": f"Bearer {token}"}, json={}
            )
    assert response.status_code == 401


def exchange_app(config):
    """Rebuild the app with an RFC 8693 exchanger trusted."""
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
        "aud": ["keycloak-mcp", "realm-management"],
        "azp": "mcp-context-forge",
        "scope": "exchange-keycloak-mcp",
    }
    claims.update(changes)
    return mint(user, **claims)


@pytest.mark.asyncio
async def test_exchanged_token_from_trusted_gateway(oauth_env):
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
    assert [r.headers["authorization"] for r in admin_calls] == ["Bearer " + token]
    assert issued[token] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"azp": "keycloak-mcp"},
        {"azp": "unrelated-client"},
        {"azp": "unrelated-client", "scope": "openid"},
        {"aud": ["mcp-context-forge"]},
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
async def test_auth_mode_ignores_shared_test_tokens(oauth_env):
    _, app, _, _, _, requests, _ = oauth_env
    async with app.router.lifespan_context(app):
        with service.keycloak_user_access_token("shared-test-token"):
            with pytest.raises(ToolError, match="Sign in"):
                await keycloak_tools.search_users(username="alice")
    assert requests == []


@pytest.mark.asyncio
async def test_exchanged_token_needs_a_trusted_exchanger(oauth_env):
    """Without KEYCLOAK_TRUSTED_EXCHANGERS a gateway's token is refused, even with openid."""
    _, app, browser, _, mint, requests, _ = oauth_env
    token = exchanged(mint, "alice", scope="openid exchange-keycloak-mcp")
    async with app.router.lifespan_context(app):
        async with browser() as client:
            response = await client.post(
                "/mcp", headers={"Authorization": "Bearer " + token}, json={}
            )
    assert response.status_code == 401
    assert not [r for r in requests if "/admin/" in r.url.path]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"KEYCLOAK_OAUTH_CLIENT_ID": ""}, "KEYCLOAK_OAUTH_CLIENT_ID"),
        ({"KEYCLOAK_REALM": ""}, "KEYCLOAK_REALM"),
        ({"KEYCLOAK_OAUTH_AUDIENCE": ""}, "KEYCLOAK_OAUTH_AUDIENCE"),
        ({"KEYCLOAK_DEV_ACCESS_TOKEN": "dev-token"}, "KEYCLOAK_DEV_ACCESS_TOKEN"),
        ({"MCP_HOST_ENDPOINT": "http://mcp.example"}, "MCP_HOST_ENDPOINT"),
        ({"MCP_HOST_ENDPOINT": "https://mcp.example/mcp"}, "MCP_HOST_ENDPOINT"),
        ({"KEYCLOAK_SERVER_URL": "http://keycloak.example"}, "HTTPS"),
    ],
)
def test_startup_rejects_unsafe_configuration(oauth_env, changes, message):
    config = oauth_env[0].model_copy(update=changes)
    with pytest.raises(ValueError, match=message):
        auth_module.create_keycloak_auth(config)


def test_jwks_fetch_trusts_the_configured_ca_bundle(oauth_env, monkeypatch):
    calls = []
    default_context = ssl.create_default_context

    def context(cafile=None):
        calls.append(cafile)
        return default_context()

    monkeypatch.setattr(auth_module.ssl, "create_default_context", context)
    config = oauth_env[0].model_copy(update={"KEYCLOAK_CA_BUNDLE": "/etc/ca.pem"})
    auth = auth_module.create_keycloak_auth(config)
    assert calls == ["/etc/ca.pem"]
    assert auth.token_verifier._http_client is auth.verification_client


@pytest.mark.asyncio
@pytest.mark.parametrize("exchangers", [[], ["mcp-context-forge"]])
async def test_shutdown_closes_the_jwks_client(oauth_env, exchangers):
    config = oauth_env[0].model_copy(update={"KEYCLOAK_TRUSTED_EXCHANGERS": exchangers})
    app = api_module.create_app(config)
    async with app.router.lifespan_context(app):
        pass
    auth = app.state.keycloak_auth
    provider = auth.provider if exchangers else auth
    assert provider.verification_client.is_closed

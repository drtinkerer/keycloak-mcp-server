"""Exercise real MCP tools and SDK requests against simulated Keycloak HTTP responses."""

import asyncio

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import SecretStr, ValidationError

from keycloak_mcp_server.src import mcp as mcp_module
from keycloak_mcp_server.src.keycloak_api import service
from keycloak_mcp_server.src.mcp import KeycloakMCPServer
from keycloak_mcp_server.src.settings import Settings
from keycloak_mcp_server.src.tools import keycloak_tools


@pytest.fixture(autouse=True)
def mock_imports():
    """Override the conftest fixture: these tests use real dependencies."""
    yield


@pytest.fixture
def keycloak_http(monkeypatch):
    """Record real SDK HTTP requests without using an Keycloak server or credentials."""
    config = Settings(
        ENABLE_AUTH=False,
        ENVIRONMENT="development",
        MCP_HOST="localhost",
        KEYCLOAK_SERVER_URL="https://keycloak.example/",
        KEYCLOAK_REALM="employees",
        KEYCLOAK_DEV_ACCESS_TOKEN="dev-user-token",
    )
    monkeypatch.setattr(service, "settings", config)
    # Older tests reload settings while its dependencies are mocked.
    monkeypatch.setattr(mcp_module, "settings", config)
    monkeypatch.setattr(mcp_module, "FastMCP", FastMCP)
    requests = []
    responses = {
        "/admin/realms/employees": {
            "realm": "employees",
            "enabled": True,
            "privateKey": "DO_NOT_RETURN",
            "smtpServer": {"password": "DO_NOT_RETURN"},
        },
        "/admin/realms/employees/users": [
            {
                "id": "user-1",
                "username": "alice",
                "enabled": True,
                "credentials": [{"secretData": "DO_NOT_RETURN"}],
                "attributes": {"private": "DO_NOT_RETURN"},
            }
        ],
        "/admin/realms/employees/users/user-1": {
            "id": "user-1",
            "username": "alice",
            "enabled": True,
            "credentials": [{"secretData": "DO_NOT_RETURN"}],
        },
        "/admin/realms/employees/users/user-1/groups": [
            {"id": "group-1", "name": "engineering", "path": "/engineering"}
        ],
        "/admin/realms/employees/groups": [
            {
                "id": "group-1",
                "name": "engineering",
                "subGroupCount": 500,
                "subGroups": [{"id": "nested-group", "name": "DO_NOT_RETURN"}],
            }
        ],
        "/admin/realms/employees/clients": [
            {
                "id": "client-1",
                "clientId": "portal",
                "enabled": True,
                "secret": "DO_NOT_RETURN",
                "registrationAccessToken": "DO_NOT_RETURN",
            }
        ],
        "/admin/realms/employees/roles": [
            {"id": "role-1", "name": "employee", "composite": False}
        ],
    }

    def handle(request):
        requests.append(request)
        assert request.method == "GET", (
            "Read-only tools must never issue writes or log in"
        )
        result = responses[request.url.path]
        if callable(result):
            result = result(request)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    original_client = httpx.AsyncClient
    clients = []

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        client = original_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("keycloak.connection.httpx.AsyncClient", client_factory)
    return config, requests, responses, clients


@pytest.mark.asyncio
async def test_mcp_discovery_and_all_read_only_tools(keycloak_http):
    _, requests, _, clients = keycloak_http
    async with Client(KeycloakMCPServer().mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {tool.__name__ for tool in keycloak_tools.KEYCLOAK_TOOLS}
        for function in keycloak_tools.KEYCLOAK_TOOLS:
            tool = tools[function.__name__]
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
            assert "token" not in tool.inputSchema["properties"]

        calls = [
            ("get_realm_info", {}),
            (
                "search_users",
                {"username": "alice", "exact": True, "first": 3, "max_results": 5},
            ),
            ("get_user", {"user_id": "user-1"}),
            ("get_user_groups", {"user_id": "user-1", "max_results": 5}),
            ("list_groups", {"search": "eng", "max_results": 5}),
            ("list_clients", {"client_id": "port", "search": True, "max_results": 5}),
            ("list_realm_roles", {"search": "employee", "max_results": 5}),
        ]
        for name, arguments in calls:
            result = await client.call_tool(name, arguments)
            assert not result.is_error
            assert "DO_NOT_RETURN" not in str(result.data)
            assert result.data["realm"] == "employees"

        # FastMCP rejects an oversized page before any upstream request is made.
        with pytest.raises(Exception, match="100"):
            await client.call_tool("search_users", {"max_results": 101})

    assert len(requests) == 7  # No automatic pagination or recursive group calls.
    assert all(
        request.headers["authorization"] == "Bearer dev-user-token"
        for request in requests
    )
    assert dict(requests[1].url.params) == {
        "first": "3",
        "max": "5",
        "username": "alice",
        "exact": "true",
    }
    assert requests[4].url.params["populateHierarchy"] == "false"
    assert requests[5].url.params["clientId"] == "port"
    assert requests[5].url.params["search"] == "true"
    assert requests[6].url.params["search"] == "employee"
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_diagnostic_tools_read_bounded_fields_as_the_user(keycloak_http):
    _, requests, responses, clients = keycloak_http
    base = "/admin/realms/employees"
    responses.update(
        {
            f"{base}/clients": [
                {"id": "client-0", "clientId": "portal-old"},
                {
                    "id": "client-1",
                    "clientId": "portal",
                    "secret": "DO_NOT_RETURN",
                    "fullScopeAllowed": True,
                    "defaultClientScopes": ["profile"],
                    "attributes": {
                        "pkce.code.challenge.method": "S256",
                        "client.secret.creation.time": "DO_NOT_RETURN",
                    },
                    "protocolMappers": [{"name": "groups"}],
                },
            ],
            f"{base}/clients/client-1/protocol-mappers/models": [
                {
                    "id": "m-1",
                    "name": "groups",
                    "protocolMapper": "oidc-group-membership-mapper",
                    "config": {
                        "claim.name": "groups",
                        "client.secret": "DO_NOT_RETURN",
                    },
                }
            ],
            f"{base}/client-scopes": [
                {
                    "id": "s-1",
                    "name": "profile",
                    "attributes": {"include.in.token.scope": "true"},
                    "protocolMappers": [{"name": "username"}],
                }
            ],
            f"{base}/users/user-1/role-mappings": {
                "realmMappings": [{"name": "offline_access"}],
                "clientMappings": {"account": {"mappings": [{"name": "view-profile"}]}},
            },
            f"{base}/users/user-1/role-mappings/realm/composite": [
                {"name": "offline_access"},
                {"name": "default-roles-employees"},
            ],
            f"{base}/users/user-1/role-mappings/clients/client-1/composite": [
                {"name": "view-clients"}
            ],
            f"{base}/groups/group-1/role-mappings": {
                "clientMappings": {
                    "realm-management": {"mappings": [{"name": "realm-admin"}]}
                }
            },
            f"{base}/groups/group-1/members": [
                {"id": "user-1", "username": "alice", "credentials": "DO_NOT_RETURN"}
            ],
            f"{base}/clients/client-1/roles/realm-admin/users": [],
            f"{base}/clients/client-1/roles/realm-admin/groups": [
                {"id": "group-1", "name": "admins", "path": "/admins"}
            ],
            f"{base}/events": [
                {
                    "time": 1,
                    "type": "LOGIN_ERROR",
                    "clientId": "portal",
                    "error": "invalid_request",
                    "details": {"reason": "Missing parameter: code_challenge_method"},
                }
            ],
            f"{base}/admin-events": [
                {
                    "time": 2,
                    "operationType": "UPDATE",
                    "resourceType": "CLIENT",
                    "resourcePath": "clients/client-1",
                    "representation": "DO_NOT_RETURN",
                    "authDetails": {"userId": "user-1", "realmId": "master"},
                }
            ],
            f"{base}/users/user-1/sessions": [
                {"id": "sess-1", "username": "alice", "clients": {"client-1": "portal"}}
            ],
            f"{base}/clients/client-1/user-sessions": [
                {"id": "sess-1", "username": "alice"}
            ],
            f"{base}/clients/client-1/evaluate-scopes/generate-example-access-token": {
                "aud": ["portal"],
                "resource_access": {"realm-management": {"roles": ["view-clients"]}},
            },
            f"{base}/components": lambda request: (
                [
                    {
                        "id": "ldap-1",
                        "name": "homelab-ldap",
                        "providerId": "ldap",
                        "config": {
                            "connectionUrl": ["ldap://ipa"],
                            "bindCredential": ["DO_NOT_RETURN"],
                        },
                    },
                    {"id": "kerb-1", "name": "kerberos", "providerId": "kerberos"},
                ]
                if "parent" not in request.url.params
                else [
                    {
                        "name": "groups",
                        "providerId": "group-ldap-mapper",
                        "config": {"groups.dn": ["cn=groups"]},
                    }
                ]
            ),
        }
    )
    async with Client(KeycloakMCPServer().mcp) as client:

        async def call(name, arguments):
            result = await client.call_tool(name, arguments)
            assert not result.is_error
            assert "DO_NOT_RETURN" not in str(result.data)
            return result.data

        detail = (await call("get_client", {"client_id": "portal"}))["client"]
        assert detail["id"] == "client-1"  # Exact clientId, not the first match.
        assert detail["attributes"] == {"pkce.code.challenge.method": "S256"}
        assert detail["protocolMapperNames"] == ["groups"]
        mappers = await call("list_client_protocol_mappers", {"client_id": "portal"})
        assert mappers["items"][0]["config"] == {"claim.name": "groups"}
        scopes = await call("list_client_scopes", {})
        assert scopes["items"][0]["protocolMapperNames"] == ["username"]
        roles = await call(
            "get_user_role_mappings",
            {"user_id": "user-1", "client_id": "portal"},
        )
        assert roles["direct"] == {
            "realm_roles": ["offline_access"],
            "client_roles": {"account": ["view-profile"]},
        }
        assert roles["effective_realm_roles"] == [
            "default-roles-employees",
            "offline_access",
        ]
        assert roles["effective_client_roles"] == {"portal": ["view-clients"]}
        group_roles = await call("get_group_role_mappings", {"group_id": "group-1"})
        assert group_roles["client_roles"] == {"realm-management": ["realm-admin"]}
        members = await call("list_group_members", {"group_id": "group-1"})
        assert members["items"] == [{"id": "user-1", "username": "alice"}]
        holders = await call(
            "find_users_with_role",
            {"role_name": "realm-admin", "client_id": "portal"},
        )
        assert holders["groups"][0]["name"] == "admins"
        events = await call(
            "list_events",
            {
                "event_types": ["LOGIN_ERROR", "CODE_TO_TOKEN_ERROR"],
                "date_from": "2026-10-01",
            },
        )
        assert events["items"][0]["details"]["reason"].startswith("Missing")
        admin_events = await call("list_admin_events", {"operation_types": ["UPDATE"]})
        assert admin_events["items"][0]["auth"] == {"userId": "user-1"}
        sessions = await call("list_user_sessions", {"user_id": "user-1"})
        assert sessions["items"][0]["clients"] == {"client-1": "portal"}
        await call("list_client_sessions", {"client_id": "portal"})
        token = await call(
            "preview_access_token",
            {"client_id": "portal", "user_id": "user-1", "scope": "openid groups"},
        )
        assert token["claims"]["aud"] == ["portal"]
        ldap = await call("get_ldap_federation", {})
        assert [item["name"] for item in ldap["items"]] == ["homelab-ldap"]
        assert ldap["items"][0]["config"] == {"connectionUrl": "ldap://ipa"}
        assert ldap["items"][0]["mappers"][0]["config"] == {"groups.dn": "cn=groups"}

        # Path-like IDs and malformed filters fail validation before any request.
        before = len(requests)
        for name, arguments in [
            ("list_events", {"date_from": "yesterday"}),
            ("list_events", {"event_types": ["login error"]}),
            (
                "preview_access_token",
                {"client_id": "portal", "user_id": "u", "scope": "a\nb"},
            ),
        ]:
            with pytest.raises(Exception):
                await client.call_tool(name, arguments)
        assert len(requests) == before

    assert all(r.headers["authorization"] == "Bearer dev-user-token" for r in requests)
    by_path = {r.url.path.removeprefix(base): r.url.params for r in requests}
    assert by_path["/events"].get_list("type") == ["LOGIN_ERROR", "CODE_TO_TOKEN_ERROR"]
    assert by_path["/events"]["dateFrom"] == "2026-10-01"
    assert "client" not in by_path["/events"]  # None filters are not sent.
    assert by_path["/admin-events"].get_list("operationTypes") == ["UPDATE"]
    assert dict(
        by_path["/clients/client-1/evaluate-scopes/generate-example-access-token"]
    ) == {"scope": "openid groups", "userId": "user-1"}
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_unknown_client_id_is_reported_without_guessing(keycloak_http):
    _, requests, _, _ = keycloak_http
    with pytest.raises(ToolError, match="No client with clientId 'port'"):
        await keycloak_tools.get_client("port")
    assert len(requests) == 1
    assert requests[0].url.params["search"] == "false"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, message",
    [
        (401, "rejected"),
        (403, "permission"),
        (404, "not found"),
        (429, "rate-limiting"),
        (500, "failed"),
    ],
)
async def test_upstream_errors_do_not_leak_response_body(keycloak_http, code, message):
    _, requests, responses, _ = keycloak_http
    responses["/admin/realms/employees/users/user-1"] = httpx.Response(
        code, json={"message": "DO_NOT_RETURN dev-user-token"}
    )
    with pytest.raises(ToolError, match=message) as error:
        await keycloak_tools.get_user("user-1")
    assert "DO_NOT_RETURN" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_missing_configuration_and_token_do_not_make_requests(keycloak_http):
    config, requests, _, _ = keycloak_http
    config.KEYCLOAK_SERVER_URL = None
    with pytest.raises(ToolError, match="Configure KEYCLOAK_SERVER_URL"):
        await keycloak_tools.get_realm_info()
    config.KEYCLOAK_SERVER_URL = "https://keycloak.example/"
    config.KEYCLOAK_DEV_ACCESS_TOKEN = None
    with pytest.raises(ToolError, match="No Keycloak user token"):
        await keycloak_tools.get_realm_info()
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [("ENVIRONMENT", "production"), ("MCP_HOST", "0.0.0.0")],
)
async def test_development_token_cannot_be_used_in_shared_server(
    keycloak_http, field, value
):
    config, requests, _, _ = keycloak_http
    setattr(config, field, value)
    with pytest.raises(ToolError, match="allowed only"):
        await keycloak_tools.get_realm_info()
    assert requests == []


@pytest.mark.asyncio
async def test_concurrent_user_tokens_remain_isolated(keycloak_http):
    config, requests, _, _ = keycloak_http
    config.ENABLE_AUTH = False
    config.KEYCLOAK_DEV_ACCESS_TOKEN = None

    async def read_as(token):
        with service.keycloak_user_access_token(token):
            await keycloak_tools.get_user("user-1")

    await asyncio.gather(read_as("alice-token"), read_as("bob-token"))
    assert {request.headers["authorization"] for request in requests} == {
        "Bearer alice-token",
        "Bearer bob-token",
    }
    with pytest.raises(ToolError, match="No Keycloak user token"):
        await keycloak_tools.get_user("user-1")


@pytest.mark.asyncio
async def test_invalid_filters_and_path_segments_fail_before_network(keycloak_http):
    _, requests, _, _ = keycloak_http
    with pytest.raises(ToolError, match="filters"):
        await keycloak_tools.search_users(search="alice", username="alice")
    with pytest.raises(ToolError, match="exact"):
        await keycloak_tools.search_users(search="alice", exact=True)
    with pytest.raises(ToolError, match="between 1 and 100"):
        await keycloak_tools.list_clients(max_results=101)
    with pytest.raises(ToolError, match="client_id filter"):
        await keycloak_tools.list_clients(search=True)
    for value in ("", "..", ".", "user\nname"):
        with pytest.raises(ToolError):
            await keycloak_tools.get_user(value)
    assert requests == []
    assert service.path_segment("f:provider:alice") == "f%3Aprovider%3Aalice"
    assert service.path_segment("alice/other?x=1") == "alice%2Fother%3Fx%3D1"


def test_configuration_rejects_url_credentials():
    with pytest.raises(ValidationError, match="must not contain"):
        Settings(KEYCLOAK_SERVER_URL="https://user:password@keycloak.example/")
    config = Settings(KEYCLOAK_DEV_ACCESS_TOKEN="DO_NOT_RETURN")
    assert isinstance(config.KEYCLOAK_DEV_ACCESS_TOKEN, SecretStr)
    assert "DO_NOT_RETURN" not in repr(config)

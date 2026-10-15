"""Request-scoped, read-only access to the Keycloak Admin REST API."""

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator, Literal
from urllib.parse import quote

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from keycloak import KeycloakAdmin, KeycloakOpenIDConnection
from keycloak.connection import ConnectionManager
from keycloak.exceptions import (
    KeycloakError,
    KeycloakGetError,
    raise_error_from_response,
)

from keycloak_mcp_server.src.keycloak_api.local_login import LocalLogin
from keycloak_mcp_server.src.settings import settings

_user_access_token: ContextVar[str | None] = ContextVar(
    "keycloak_user_access_token", default=None
)


@contextmanager
def keycloak_user_access_token(token: str) -> Iterator[None]:
    """Bind a local test token to one task; ignored when OAuth is enabled."""
    handle = _user_access_token.set(token)
    try:
        yield
    finally:
        _user_access_token.reset(handle)


def path_segment(value: str) -> str:
    """Encode identifiers so tool arguments cannot change the API route."""
    if not value or value.strip() != value or value in {".", ".."}:
        raise ToolError("Provide a non-empty realm or resource ID.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ToolError("Realm and resource IDs must not contain control characters.")
    return quote(value, safe="")


def pagination(first: int, max_results: int) -> dict[str, int]:
    """Enforce page limits even when a tool is called directly from Python."""
    if not 0 <= first or not 1 <= max_results <= 100:
        raise ToolError(
            "first must be non-negative; max_results must be between 1 and 100."
        )
    return {"first": first, "max": max_results}


class DelegatedConnection(KeycloakOpenIDConnection):
    """Use only the supplied user token; the OAuth layer owns token renewal."""

    async def a_raw_get(self, path: str, **kwargs: Any):
        """Perform a GET without the SDK’s automatic login or token renewal."""
        # The SDK normally tries a new login after a 401. Never switch identities
        # or silently obtain service-account credentials for a delegated request.
        return await ConnectionManager.a_raw_get(self, path, **kwargs)


class KeycloakService:
    """Keep realm selection and Admin API calls outside MCP tool functions."""

    def __init__(self, admin: KeycloakAdmin, realm: str):
        """Bind this invocation to a delegated SDK connection and realm."""
        self.admin = admin
        self.realm = realm

    async def get_realm(self) -> dict:
        """Read the configured realm’s representation."""
        return await self.admin.a_get_realm(path_segment(self.realm))

    async def search_users(self, query: dict) -> list:
        """Search users with the supplied bounded query."""
        return await self.admin.a_get_users(query)

    async def get_user(self, user_id: str) -> dict:
        """Read a user by their encoded internal ID."""
        return await self.admin.a_get_user(path_segment(user_id))

    async def get_user_groups(self, user_id: str, query: dict) -> list:
        """Read a bounded page of the user’s group memberships."""
        return await self.admin.a_get_user_groups(path_segment(user_id), query=query)

    async def list_page(
        self, resource: Literal["groups", "clients", "roles"], query: dict
    ) -> list:
        """Read one page without recursively expanding or fetching every result."""
        # The SDK's group helper expands child groups, its client helper fetches
        # all clients, and its paginated role helper drops the search parameter.
        # Use its authenticated connection for a single bounded GET instead.
        return await self.get_json(resource, query=query)

    async def get_json(
        self, *segments: str, query: dict | None = None, expect: type = list
    ) -> Any:
        """GET one realm Admin API resource; every path segment is encoded."""
        path = "/".join(path_segment(segment) for segment in segments)
        response = await self.admin.connection.a_raw_get(
            f"admin/realms/{path_segment(self.realm)}/{path}", **(query or {})
        )
        result = raise_error_from_response(
            response, KeycloakGetError, expected_codes=[200]
        )
        if not isinstance(result, expect):
            raise ToolError("Keycloak returned an unexpected response shape.")
        return result

    async def get_client(self, client_id: str) -> dict:
        """Resolve a clientId (the application's name) to its representation."""
        matches = await self.get_json(
            "clients", query={"clientId": client_id, "search": False, "max": 2}
        )
        for client in matches:
            if isinstance(client, dict) and client.get("clientId") == client_id:
                return client
        raise ToolError(f"No client with clientId {client_id!r} in this realm.")


_local_login: LocalLogin | None = None


def get_local_login() -> LocalLogin:
    """Return the process-wide stdio sign-in session (one local user)."""
    global _local_login
    if _local_login is None:
        _local_login = LocalLogin(settings)
    return _local_login


@asynccontextmanager
async def get_keycloak_service() -> AsyncIterator[KeycloakService]:
    """Create an isolated SDK connection for a tool invocation."""
    if not settings.KEYCLOAK_SERVER_URL or not settings.KEYCLOAK_REALM:
        raise ToolError(
            "Configure KEYCLOAK_SERVER_URL and KEYCLOAK_REALM before calling Keycloak tools."
        )

    stdio = settings.MCP_TRANSPORT_PROTOCOL == "stdio"
    token: str | None
    if stdio:
        # The server runs as one local user, who signs in through the browser.
        token = await get_local_login().access_token()
    elif settings.ENABLE_AUTH:
        access = get_access_token()
        if access is None:
            raise ToolError(
                "Sign in through your MCP client before calling Keycloak tools."
            )
        token = access.token
    else:
        token = _user_access_token.get()
    if not token and settings.KEYCLOAK_DEV_ACCESS_TOKEN and not stdio:
        if (
            settings.ENABLE_AUTH
            or settings.ENVIRONMENT != "development"
            or settings.MCP_HOST not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ToolError(
                "KEYCLOAK_DEV_ACCESS_TOKEN is allowed only with ENABLE_AUTH=False, "
                "ENVIRONMENT=development, and an MCP server bound to localhost."
            )
        token = settings.KEYCLOAK_DEV_ACCESS_TOKEN.get_secret_value()
    if not token:
        raise ToolError(
            "No Keycloak user token is available. Configure browser OAuth, or use "
            "KEYCLOAK_DEV_ACCESS_TOKEN for localhost development with authentication disabled."
        )
    if any(char.isspace() for char in token):
        raise ToolError(
            "Supply the access token alone, without a Bearer prefix or whitespace."
        )

    connection = DelegatedConnection(
        server_url=str(settings.KEYCLOAK_SERVER_URL).rstrip("/") + "/",
        realm_name=path_segment(settings.KEYCLOAK_REALM),
        token={"access_token": token, "expires_in": 0},
        verify=settings.KEYCLOAK_CA_BUNDLE or True,
        timeout=settings.KEYCLOAK_TIMEOUT,
        max_retries=0,
    )
    try:
        yield KeycloakService(
            KeycloakAdmin(connection=connection), settings.KEYCLOAK_REALM
        )
    except KeycloakError as exc:
        code = exc.response_code
        if stdio and code == 401:
            get_local_login().invalidate_access_token()
        messages: dict[int | None, str] = {
            401: (
                "Keycloak rejected the access token. Call the tool again to refresh it."
                if stdio
                else "Keycloak rejected the access token. Sign in again or supply a fresh development token."
            ),
            403: "Your Keycloak identity does not have permission for this operation.",
            404: "The requested realm or resource was not found in Keycloak.",
            429: "Keycloak is rate-limiting requests. Retry later.",
        }
        raise ToolError(
            messages.get(
                code,
                "The Keycloak Admin API request failed. Check connectivity and server configuration.",
            )
        ) from None
    finally:
        await connection.aclose()

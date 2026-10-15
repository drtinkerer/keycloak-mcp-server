"""Read-only MCP tools for one configured Keycloak realm."""

import re
from typing import Annotated, Any, Literal

from pydantic import Field

from keycloak_mcp_server.src.keycloak_api.service import (
    get_keycloak_service,
    pagination,
)

Offset = Annotated[int, Field(ge=0, description="Pagination offset.")]
PageSize = Annotated[
    int, Field(ge=1, le=100, description="Maximum items to return (1–100).")
]
ResourceID = Annotated[str, Field(min_length=1, max_length=512)]
Search = Annotated[str | None, Field(max_length=256)]
ClientID = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        description="The application's clientId (e.g. argocd), not its internal UUID.",
    ),
]
RoleName = Annotated[str, Field(min_length=1, max_length=256)]
Date = Annotated[
    str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD.")
]
EventType = Annotated[str, Field(pattern=r"^[A-Z_]{1,64}$")]
Scope = Annotated[
    str,
    Field(
        pattern=r"^[\x21-\x7E]+( [\x21-\x7E]+)*$",
        max_length=512,
        description="Space-separated scopes to request.",
    ),
]

USER_FIELDS = (
    "id",
    "username",
    "firstName",
    "lastName",
    "email",
    "enabled",
    "emailVerified",
    "createdTimestamp",
    "federationLink",
    "requiredActions",
)
GROUP_FIELDS = ("id", "name", "path", "parentId", "subGroupCount")
CLIENT_FIELDS = (
    "id",
    "clientId",
    "name",
    "description",
    "enabled",
    "protocol",
    "publicClient",
    "bearerOnly",
    "standardFlowEnabled",
    "directAccessGrantsEnabled",
    "serviceAccountsEnabled",
    "redirectUris",
    "webOrigins",
)
ROLE_FIELDS = ("id", "name", "description", "composite", "clientRole", "containerId")
REALM_FIELDS = (
    "id",
    "realm",
    "displayName",
    "enabled",
    "sslRequired",
    "registrationAllowed",
    "loginWithEmailAllowed",
    "duplicateEmailsAllowed",
    "resetPasswordAllowed",
    "verifyEmail",
    "bruteForceProtected",
    "accessTokenLifespan",
    "ssoSessionIdleTimeout",
    "ssoSessionMaxLifespan",
)


CLIENT_DETAIL_FIELDS = CLIENT_FIELDS + (
    "implicitFlowEnabled",
    "fullScopeAllowed",
    "consentRequired",
    "frontchannelLogout",
    "rootUrl",
    "baseUrl",
    "adminUrl",
    "defaultClientScopes",
    "optionalClientScopes",
)
CLIENT_ATTRIBUTES = (
    "pkce.code.challenge.method",
    "post.logout.redirect.uris",
    "access.token.lifespan",
    "client.session.idle.timeout",
    "client.session.max.lifespan",
    "use.refresh.tokens",
    "standard.token.exchange.enabled",
    "oauth2.device.authorization.grant.enabled",
)
MAPPER_FIELDS = ("id", "name", "protocol", "protocolMapper")
CLIENT_SCOPE_FIELDS = ("id", "name", "protocol", "description")
EVENT_FIELDS = ("time", "type", "clientId", "userId", "ipAddress", "error", "details")
ADMIN_EVENT_FIELDS = ("time", "operationType", "resourceType", "resourcePath", "error")
SESSION_FIELDS = (
    "id",
    "username",
    "userId",
    "ipAddress",
    "start",
    "lastAccess",
    "rememberMe",
    "clients",
)
LDAP_PROVIDER_TYPE = "org.keycloak.storage.UserStorageProvider"
LDAP_MAPPER_TYPE = "org.keycloak.storage.ldap.mappers.LDAPStorageMapper"
# Config keys that can hold credentials (bindCredential, client secrets, ...).
SECRET_KEY = re.compile(r"secret|password|credential|private", re.IGNORECASE)


def select_fields(value: dict, fields: tuple[str, ...]) -> dict:
    """Return known fields instead of exposing complete admin representations."""
    return {key: value[key] for key in fields if key in value}


def page_result(
    realm: str, items: list, fields: tuple[str, ...], first: int, max_results: int
) -> dict:
    """Select safe fields and attach metadata for a bounded result page."""
    return {
        "realm": realm,
        "items": [select_fields(item, fields) for item in items[:max_results]],
        "first": first,
        "max_results": max_results,
        "returned_count": min(len(items), max_results),
    }


def safe_config(config: dict | None) -> dict:
    """Flatten component/mapper config and drop anything that may be a secret."""
    result = {}
    for key, value in (config or {}).items():
        if SECRET_KEY.search(key):
            continue
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        result[key] = value
    return result


def role_names(roles: list) -> list[str]:
    """Reduce role representations to sorted names."""
    return sorted(role["name"] for role in roles if isinstance(role, dict))


def role_mappings(mappings: dict) -> dict:
    """Summarise a role-mappings representation as role names per container."""
    return {
        "realm_roles": role_names(mappings.get("realmMappings") or []),
        "client_roles": {
            client: role_names(entry.get("mappings") or [])
            for client, entry in sorted((mappings.get("clientMappings") or {}).items())
        },
    }


async def get_realm_info() -> dict[str, Any]:
    """Read login and session settings for the configured Keycloak realm. Excludes keys, SMTP credentials, and other secrets."""
    async with get_keycloak_service() as service:
        return select_fields(await service.get_realm(), REALM_FIELDS)


async def search_users(
    search: Search = None,
    username: Search = None,
    email: Search = None,
    exact: bool = False,
    first: Offset = 0,
    max_results: PageSize = 20,
) -> dict[str, Any]:
    """Search users in the configured Keycloak realm. Use search for free text OR username/email filters. exact applies to username/email only. Returns one page, excluding credentials and custom attributes."""
    from fastmcp.exceptions import ToolError

    if search is not None and (username is not None or email is not None):
        raise ToolError(
            "Use search or username/email filters, rather than combining them."
        )
    if exact and search is not None:
        raise ToolError(
            "exact applies to username/email filters, not free-text search."
        )
    query: dict[str, Any] = pagination(first, max_results)
    for key, value in {"search": search, "username": username, "email": email}.items():
        if value is not None:
            query[key] = value
    if username is not None or email is not None:
        query["exact"] = exact
    async with get_keycloak_service() as service:
        return page_result(
            service.realm,
            await service.search_users(query),
            USER_FIELDS,
            first,
            max_results,
        )


async def get_user(user_id: ResourceID) -> dict[str, Any]:
    """Read a user by its internal Keycloak ID (not username). Use search_users to find the ID. Excludes credentials and custom attributes."""
    async with get_keycloak_service() as service:
        return {
            "realm": service.realm,
            "user": select_fields(await service.get_user(user_id), USER_FIELDS),
        }


async def get_user_groups(
    user_id: ResourceID, first: Offset = 0, max_results: PageSize = 20
) -> dict[str, Any]:
    """Read one page of a user's group memberships. Requires the internal user ID from search_users."""
    query = pagination(first, max_results)
    async with get_keycloak_service() as service:
        return page_result(
            service.realm,
            await service.get_user_groups(user_id, query),
            GROUP_FIELDS,
            first,
            max_results,
        )


async def list_groups(
    search: Search = None, first: Offset = 0, max_results: PageSize = 20
) -> dict[str, Any]:
    """List one page of top-level groups, or search group names in the configured Keycloak realm. Does not recursively expand the group hierarchy."""
    query: dict[str, Any] = {
        **pagination(first, max_results),
        "briefRepresentation": True,
        "populateHierarchy": False,
    }
    if search is not None:
        query["search"] = search
    async with get_keycloak_service() as service:
        return page_result(
            service.realm,
            await service.list_page("groups", query),
            GROUP_FIELDS,
            first,
            max_results,
        )


async def list_clients(
    client_id: Search = None,
    search: bool = False,
    first: Offset = 0,
    max_results: PageSize = 20,
) -> dict[str, Any]:
    """List one page of OIDC/SAML clients. Filter by clientId (the application's client name): exact match by default, or substring match with search=true. Excludes client secrets and registration tokens."""
    from fastmcp.exceptions import ToolError

    if search and client_id is None:
        raise ToolError("search applies to a client_id filter.")
    query: dict[str, Any] = pagination(first, max_results)
    if client_id is not None:
        query["clientId"] = client_id
        query["search"] = search
    async with get_keycloak_service() as service:
        return page_result(
            service.realm,
            await service.list_page("clients", query),
            CLIENT_FIELDS,
            first,
            max_results,
        )


async def list_realm_roles(
    search: Search = None, first: Offset = 0, max_results: PageSize = 20
) -> dict[str, Any]:
    """List or search one page of realm roles. These are realm-level roles, not client roles or a user's effective role mappings."""
    query: dict[str, Any] = {
        **pagination(first, max_results),
        "briefRepresentation": True,
    }
    if search is not None:
        query["search"] = search
    async with get_keycloak_service() as service:
        return page_result(
            service.realm,
            await service.list_page("roles", query),
            ROLE_FIELDS,
            first,
            max_results,
        )


async def get_client(client_id: ClientID) -> dict[str, Any]:
    """Read one client's full configuration by clientId: enabled flows, PKCE method, full-scope setting, redirect URIs, web origins, assigned default/optional client scopes and token lifetimes. Excludes the client secret and registration token."""
    async with get_keycloak_service() as service:
        client = await service.get_client(client_id)
    detail = select_fields(client, CLIENT_DETAIL_FIELDS)
    detail["attributes"] = select_fields(
        client.get("attributes") or {}, CLIENT_ATTRIBUTES
    )
    detail["protocolMapperNames"] = sorted(
        mapper.get("name", "") for mapper in client.get("protocolMappers") or []
    )
    return {"realm": service.realm, "client": detail}


async def list_client_protocol_mappers(client_id: ClientID) -> dict[str, Any]:
    """List the protocol mappers defined directly on a client, i.e. the claims it adds to tokens (groups, audience, attributes). Mappers inherited from client scopes are listed by list_client_scopes."""
    async with get_keycloak_service() as service:
        client = await service.get_client(client_id)
        mappers = await service.get_json(
            "clients", client["id"], "protocol-mappers", "models"
        )
    return {
        "realm": service.realm,
        "client_id": client_id,
        "items": [
            {
                **select_fields(mapper, MAPPER_FIELDS),
                "config": safe_config(mapper.get("config")),
            }
            for mapper in mappers[:100]
        ],
    }


async def list_client_scopes() -> dict[str, Any]:
    """List the realm's client scopes with the protocol mappers each one contributes. Use get_client to see which scopes a client receives by default or optionally."""
    async with get_keycloak_service() as service:
        scopes = await service.get_json("client-scopes")
    return {
        "realm": service.realm,
        "items": [
            {
                **select_fields(scope, CLIENT_SCOPE_FIELDS),
                "includeInTokenScope": (scope.get("attributes") or {}).get(
                    "include.in.token.scope"
                ),
                "protocolMapperNames": sorted(
                    mapper.get("name", "")
                    for mapper in scope.get("protocolMappers") or []
                ),
            }
            for scope in scopes[:100]
        ],
    }


async def get_user_role_mappings(
    user_id: ResourceID, client_id: Search = None
) -> dict[str, Any]:
    """Explain a user's roles. 'direct' are roles assigned to the user itself. 'effective_realm_roles' also include roles inherited from groups and composites. Pass client_id to also get the effective roles of that client (e.g. realm-management). Requires the internal user ID from search_users."""
    async with get_keycloak_service() as service:
        direct = await service.get_json("users", user_id, "role-mappings", expect=dict)
        effective_realm = await service.get_json(
            "users", user_id, "role-mappings", "realm", "composite"
        )
        result: dict[str, Any] = {
            "realm": service.realm,
            "user_id": user_id,
            "direct": role_mappings(direct),
            "effective_realm_roles": role_names(effective_realm),
        }
        if client_id is not None:
            client = await service.get_client(client_id)
            effective_client = await service.get_json(
                "users", user_id, "role-mappings", "clients", client["id"], "composite"
            )
            result["effective_client_roles"] = {client_id: role_names(effective_client)}
    return result


async def get_group_role_mappings(group_id: ResourceID) -> dict[str, Any]:
    """List the realm and client roles assigned to a group. Every member inherits them. Requires the internal group ID from list_groups."""
    async with get_keycloak_service() as service:
        mappings = await service.get_json(
            "groups", group_id, "role-mappings", expect=dict
        )
    return {"realm": service.realm, "group_id": group_id, **role_mappings(mappings)}


async def list_group_members(
    group_id: ResourceID, first: Offset = 0, max_results: PageSize = 20
) -> dict[str, Any]:
    """List one page of a group's members (for LDAP groups, read from the directory). Requires the internal group ID from list_groups."""
    query = {**pagination(first, max_results), "briefRepresentation": True}
    async with get_keycloak_service() as service:
        members = await service.get_json("groups", group_id, "members", query=query)
    return page_result(service.realm, members, USER_FIELDS, first, max_results)


async def find_users_with_role(
    role_name: RoleName,
    client_id: Search = None,
    first: Offset = 0,
    max_results: PageSize = 20,
) -> dict[str, Any]:
    """Find who holds a role: users assigned it directly, and groups assigned it (their members inherit it, see list_group_members). Pass client_id for a client role, e.g. role_name=realm-admin, client_id=realm-management."""
    query = pagination(first, max_results)
    async with get_keycloak_service() as service:
        if client_id is None:
            base: tuple[str, ...] = ("roles", role_name)
        else:
            client = await service.get_client(client_id)
            base = ("clients", client["id"], "roles", role_name)
        users = await service.get_json(*base, "users", query=query)
        groups = await service.get_json(
            *base, "groups", query={"first": 0, "max": 100, "briefRepresentation": True}
        )
    return {
        **page_result(service.realm, users, USER_FIELDS, first, max_results),
        "role": role_name,
        "client_id": client_id,
        "groups": [select_fields(group, GROUP_FIELDS) for group in groups],
    }


async def list_events(
    event_types: Annotated[list[EventType] | None, Field(max_length=20)] = None,
    client_id: Search = None,
    user_id: Search = None,
    date_from: Date = None,
    date_to: Date = None,
    first: Offset = 0,
    max_results: PageSize = 20,
) -> dict[str, Any]:
    """List recent login events, newest first, e.g. event_types=["LOGIN_ERROR"] to see why sign-ins fail (error and reason are in details). Filter by clientId, internal user ID, or date range. Empty unless the realm saves events (Realm settings, Events tab)."""
    query = {
        **pagination(first, max_results),
        "type": event_types,
        "client": client_id,
        "user": user_id,
        "dateFrom": date_from,
        "dateTo": date_to,
    }
    async with get_keycloak_service() as service:
        events = await service.get_json("events", query=query)
    return page_result(service.realm, events, EVENT_FIELDS, first, max_results)


async def list_admin_events(
    operation_types: Annotated[
        list[Literal["CREATE", "UPDATE", "DELETE", "ACTION"]] | None,
        Field(max_length=4),
    ] = None,
    resource_types: Annotated[list[EventType] | None, Field(max_length=20)] = None,
    resource_path: Search = None,
    date_from: Date = None,
    date_to: Date = None,
    first: Offset = 0,
    max_results: PageSize = 20,
) -> dict[str, Any]:
    """List the admin audit trail, newest first: who changed what (operation, resource type and path, acting user and client). Resource types are e.g. CLIENT, USER, GROUP, REALM_ROLE_MAPPING. Never returns the changed representation. Empty unless the realm saves admin events."""
    query = {
        **pagination(first, max_results),
        "operationTypes": operation_types,
        "resourceTypes": resource_types,
        "resourcePath": resource_path,
        "dateFrom": date_from,
        "dateTo": date_to,
    }
    async with get_keycloak_service() as service:
        events = await service.get_json("admin-events", query=query)
    items = []
    for event in events[:max_results]:
        auth = event.get("authDetails") or {}
        items.append(
            {
                **select_fields(event, ADMIN_EVENT_FIELDS),
                "auth": select_fields(auth, ("userId", "clientId", "ipAddress")),
            }
        )
    return {
        "realm": service.realm,
        "items": items,
        "first": first,
        "max_results": max_results,
        "returned_count": len(items),
    }


async def list_user_sessions(user_id: ResourceID) -> dict[str, Any]:
    """List a user's active sessions: IP address, start and last access times, and the clients used in each. Requires the internal user ID from search_users."""
    async with get_keycloak_service() as service:
        sessions = await service.get_json("users", user_id, "sessions")
    return page_result(service.realm, sessions, SESSION_FIELDS, 0, 100)


async def list_client_sessions(
    client_id: ClientID, first: Offset = 0, max_results: PageSize = 20
) -> dict[str, Any]:
    """List one page of active user sessions for a client, i.e. who is currently signed in to that application."""
    async with get_keycloak_service() as service:
        client = await service.get_client(client_id)
        sessions = await service.get_json(
            "clients",
            client["id"],
            "user-sessions",
            query=pagination(first, max_results),
        )
    return page_result(service.realm, sessions, SESSION_FIELDS, first, max_results)


async def preview_access_token(
    client_id: ClientID, user_id: ResourceID, scope: Scope = "openid"
) -> dict[str, Any]:
    """Show the claims of the access token a client would get for a user (audience, scopes, realm and client roles, groups), without signing anyone in. Use it to debug 'why is this role or claim missing'. Requires the internal user ID from search_users."""
    async with get_keycloak_service() as service:
        client = await service.get_client(client_id)
        claims = await service.get_json(
            "clients",
            client["id"],
            "evaluate-scopes",
            "generate-example-access-token",
            query={"scope": scope, "userId": user_id},
            expect=dict,
        )
    return {
        "realm": service.realm,
        "client_id": client_id,
        "user_id": user_id,
        "scope": scope,
        "claims": claims,
    }


async def get_ldap_federation() -> dict[str, Any]:
    """Show the realm's LDAP user federation: connection URL, DNs, edit mode, sync periods and last sync time, plus every LDAP mapper (attributes, groups, roles). Bind credentials are never returned."""
    async with get_keycloak_service() as service:
        providers = await service.get_json(
            "components", query={"type": LDAP_PROVIDER_TYPE}
        )
        items = []
        for provider in providers:
            if provider.get("providerId") != "ldap":
                continue
            mappers = await service.get_json(
                "components",
                query={"parent": provider["id"], "type": LDAP_MAPPER_TYPE},
            )
            items.append(
                {
                    "id": provider.get("id"),
                    "name": provider.get("name"),
                    "config": safe_config(provider.get("config")),
                    "mappers": [
                        {
                            "name": mapper.get("name"),
                            "type": mapper.get("providerId"),
                            "config": safe_config(mapper.get("config")),
                        }
                        for mapper in mappers
                    ],
                }
            )
    return {"realm": service.realm, "items": items}


KEYCLOAK_TOOLS = (
    get_realm_info,
    search_users,
    get_user,
    get_user_groups,
    list_groups,
    list_clients,
    list_realm_roles,
    get_client,
    list_client_protocol_mappers,
    list_client_scopes,
    get_user_role_mappings,
    get_group_role_mappings,
    list_group_members,
    find_users_with_role,
    list_events,
    list_admin_events,
    list_user_sessions,
    list_client_sessions,
    preview_access_token,
    get_ldap_federation,
)

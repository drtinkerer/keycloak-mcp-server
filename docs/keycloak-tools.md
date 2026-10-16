# Read-only Keycloak tools

The server provides twenty read-only Admin REST API tools, implemented with
`python-keycloak==7.1.1`.
It works with Keycloak and distributions built on it; live tool calls require the
registered client and a user login.

| Tool | Purpose |
| --- | --- |
| `get_realm_info` | Selected realm login and session settings |
| `search_users` | Search users by free text or username/email filters |
| `get_user` | Selected user details by internal user ID |
| `get_user_groups` | A page of group memberships by internal user ID |
| `list_groups` | A page of top-level groups or group-name search results |
| `list_clients` | A page of clients, filtered by exact `clientId`, or by substring with `search=true` |
| `list_realm_roles` | A page of realm roles, optionally filtered by name |
| `get_client` | One client's full config by `clientId`: flows, PKCE, full scope, scopes, lifetimes |
| `list_client_protocol_mappers` | Claims a client adds to its tokens |
| `list_client_scopes` | Realm client scopes and the mappers each contributes |
| `get_user_role_mappings` | A user's direct roles, effective realm roles, and optionally one client's effective roles |
| `get_group_role_mappings` | Roles assigned to a group |
| `list_group_members` | A page of a group's members (LDAP groups are read from the directory) |
| `find_users_with_role` | Users and groups holding a realm or client role |
| `list_events` | Login events such as `LOGIN_ERROR` (needs realm event saving) |
| `list_admin_events` | Admin audit trail, without changed representations (needs admin event saving) |
| `list_user_sessions` | A user's active sessions |
| `list_client_sessions` | Who is signed in to a client |
| `preview_access_token` | Claims a client's access token would carry for a user |
| `get_ldap_federation` | LDAP federation and mapper config, without bind credentials |

All list tools accept `first` (default 0) and `max_results` (default 20, maximum 100).
Responses include `realm`, `items`, `first`, `max_results`, and `returned_count`.
`returned_count` is the size of this page, not a total. Request subsequent pages by
incrementing `first`. Groups are not recursively expanded.

User IDs are the `id` fields returned by `search_users`, not usernames. Client
filtering uses the application-facing `clientId`, not its internal UUID. Realm roles
do not include client roles or a user's effective role mappings.

The tools return selected fields. User credentials and custom attributes, client
secrets and registration tokens, realm keys, and SMTP configuration are omitted.
All twenty tools advertise read-only and idempotent MCP annotations.

## Discover tools before creating a client

```bash
make local
```

In a second terminal:

```bash
uv run python examples/keycloak_client.py --list-only
```

Discovery needs no Keycloak configuration while MCP authentication is disabled. Calls
return a clear configuration/authentication error until a real target and token are
provided; the server does not fall back to fabricated data or a shared admin login.

## Browser sign-in and live tool testing

Configure the public PKCE `keycloak-mcp` client and server as described in the
[authentication guide](authentication.md). The direct client signs in to Keycloak
with that client on callback port 9876 (`--client-id`, `--callback-port`), so register
`http://localhost:9876/*` as a redirect URI. Each request uses that user’s validated Keycloak access token.
Target URL, realm, and client credentials are server settings, not tool arguments.

```bash
uv run python examples/keycloak_client.py --oauth --list-only
uv run python examples/keycloak_client.py --oauth --username alice
```

This direct client uses no LLM. The second command inspects realm settings, searches
for the exact username, then requests user details and memberships if found. It
also lists groups, clients, and realm roles. It exits nonzero when a call fails.
Users need the appropriate Admin API permissions for all requested endpoints; use
Codex or Claude to call individual tools if a user has access to only some of them.
See [the README](../README.md) for agent add, login, and remove commands.

## Optional localhost development token

For single-user debugging without browser OAuth, configure a short-lived **user**
Admin API access token in the ignored `.env` file:

```dotenv
ENABLE_AUTH=False
ENVIRONMENT=development
MCP_HOST=localhost
KEYCLOAK_SERVER_URL=https://keycloak.example.com
KEYCLOAK_REALM=myrealm
KEYCLOAK_DEV_ACCESS_TOKEN=<your user access token, without Bearer prefix>
# KEYCLOAK_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
```

This shortcut is allowed only with authentication disabled, development mode, and
a loopback bind address. All callers of this local server use the same token.
Remove it before enabling OAuth. Expired tokens must be replaced manually in this
mode. Restart after changing settings, then run:

```bash
uv run python examples/keycloak_client.py --username alice
```

TLS verification stays enabled in both modes. An authenticated user without the
required permissions receives a sanitized permission error. The server does not
elevate privileges, fabricate data, or switch to service-account credentials.

## Implementation

- `src/tools/keycloak_tools.py`: tool schemas, bounded pagination, and selected fields.
- `src/keycloak_api/service.py`: asynchronous SDK requests and sanitized errors.
- `src/keycloak_api/auth.py`: stateless Keycloak token verification and gateway token exchange.
- `src/api.py`: FastMCP HTTP application, OAuth routes, and lifespan.
- `tests/test_keycloak.py`: MCP calls and simulated Admin API responses.
- `tests/test_keycloak_oauth.py`: real proxy flow with simulated Keycloak OAuth endpoints.

The service creates a connection per invocation and reads the verified upstream
user token from FastMCP’s request context. It ignores the legacy development-token
context when OAuth is enabled. The proxy owns token renewal; the SDK does not
attempt another login after Keycloak rejects a supplied token.

References:

- [Python Keycloak async API](https://python-keycloak.readthedocs.io/en/stable/modules/async.html)
- [Keycloak Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html)

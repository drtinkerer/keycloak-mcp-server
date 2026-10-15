# Stateless Auth Migration Plan

Move from the current stateful `OAuthProxy` pattern to a stateless `KeycloakAuthProvider` pattern, aligning with the MCP 2026-07-28 spec.

## Current State

The server acts as both an OAuth authorization server and a resource server using FastMCP's `OAuthProxy`:

- **DCR** at `/register` creates per-session MCP clients
- **OAuth proxy** at `/authorize` and `/token` proxies Keycloak auth and issues its own MCP JWTs
- **Keycloak tokens stored** encrypted on a PVC (`FileTreeStore` + Fernet)
- **Signing key** (`KEYCLOAK_OAUTH_SIGNING_KEY`) used to sign MCP JWTs
- **Single replica** constraint due to PVC-bound state

Server-side state on disk:

| Path | Contents |
|------|----------|
| `mcp-oauth-proxy-clients/` | DCR'd MCP client registrations |
| `mcp-upstream-tokens/` | Encrypted Keycloak access tokens |
| `mcp-jti-mappings/` | MCP JWT ID to Keycloak token mappings |
| `mcp-authorization-codes/` | In-flight PKCE auth codes |
| `mcp-consent-csrf-tokens/` | Consent CSRF state |

## Target State

The server becomes a **resource server only**. It validates Keycloak JWTs directly via JWKS. No tokens stored, no state, horizontally scalable.

```
MCP Client (Claude Code)
    |
    |-- Discovers auth server via /.well-known/oauth-protected-resource/mcp
    |-- Authenticates directly with Keycloak (EmployeeIDP realm)
    |-- Gets Keycloak access token
    |
    v
keycloak-mcp-server (stateless)
    |
    |-- Validates JWT against Keycloak JWKS (no stored state)
    |-- Calls Keycloak Admin API with the user's token
    |
    v
Keycloak Admin REST API
```

## Prerequisites

- [x] RHBK 26.6.0+ on stage (confirmed: `26.6.0`)
- [ ] Decide client registration strategy (DCR vs pre-registered client)
- [ ] Verify FastMCP `KeycloakAuthProvider` works with RHBK 26.6.0

## Client Registration Strategy

### Option A: Pre-registered public client (recommended)

Create a public OIDC client in EmployeeIDP:

| Setting | Value |
|---------|-------|
| Client ID | `keycloak-mcp` (reuse existing) |
| Client Protocol | openid-connect |
| Access Type | public |
| Standard Flow | enabled |
| Valid Redirect URIs | `http://localhost:*/callback`, `http://127.0.0.1:*/callback` |
| Web Origins | `+` |

Pros: no DCR on the realm, controlled client surface, no self-registration risk.

### Option B: Enable Keycloak native DCR

Enable DCR on the EmployeeIDP realm so MCP clients can self-register.

Pros: zero manual client setup. Cons: any client can register on the realm, needs scoped DCR policies.

## Code Changes

### 1. Replace auth provider in `auth.py`

Remove:
- `KeycloakOAuthProxy` class
- `KeycloakMultiAuth` class
- `create_keycloak_auth()` function
- All `FileTreeStore`, `FernetEncryptionWrapper`, `derive_jwt_key` imports

Replace with:
```python
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

def create_keycloak_auth(config: Settings) -> KeycloakAuthProvider | None:
    if not config.ENABLE_AUTH:
        return None
    return KeycloakAuthProvider(
        realm_url=f"{config.KEYCLOAK_SERVER_URL}/realms/{config.KEYCLOAK_REALM}",
        base_url=config.MCP_HOST_ENDPOINT,
        audience=config.KEYCLOAK_OAUTH_AUDIENCE,
    )
```

If `KEYCLOAK_TRUSTED_EXCHANGERS` support is still needed, keep `KeycloakTokenVerifier` and compose via `MultiAuth` with the `KeycloakAuthProvider` as the primary — both paths are stateless.

### 2. Simplify `service.py` token handling

The `get_keycloak_service()` function currently checks for `keycloak_admin_token` in claims (set by the proxy). With direct Keycloak tokens, the access token from FastMCP is already the Keycloak token:

```python
# Before (proxy-issued MCP JWT → lookup stored Keycloak token)
access = get_access_token()
if not access.claims.get("keycloak_admin_token"):
    raise ToolError(...)
token = access.token  # This was the stored Keycloak token

# After (Keycloak JWT passed through directly)
access = get_access_token()
token = access.token  # This IS the Keycloak token
```

### 3. Remove settings

Delete from `Settings`:
- `KEYCLOAK_OAUTH_SIGNING_KEY`
- `KEYCLOAK_OAUTH_STORAGE_DIR`
- `KEYCLOAK_OAUTH_CLIENT_REDIRECT_URIS`
- `KEYCLOAK_OAUTH_CLIENT_SECRET` (public client, no secret)

Keep:
- `KEYCLOAK_SERVER_URL`
- `KEYCLOAK_REALM`
- `KEYCLOAK_OAUTH_CLIENT_ID`
- `KEYCLOAK_OAUTH_AUDIENCE`
- `KEYCLOAK_OAUTH_SCOPES`
- `KEYCLOAK_TRUSTED_EXCHANGERS` (if gateway flow still needed)
- `ENABLE_AUTH`

### 4. Simplify `api.py`

The `lifespan` cleanup (`auth.aclose()`) is still needed for the JWKS HTTP client but the `FileTreeStore` lifecycle is gone.

## Deployment Changes

### Remove from kustomize/overlay

- `PersistentVolumeClaim` `keycloak-mcp-data`
- `Secret` `keycloak-mcp-signing-key`
- `volumeMounts` and `volumes` from the Deployment

### Update ConfigMap `keycloak-mcp-config`

Remove:
- `KEYCLOAK_OAUTH_STORAGE_DIR`

Keep unchanged:
- `KEYCLOAK_SERVER_URL`
- `KEYCLOAK_REALM`
- `KEYCLOAK_OAUTH_CLIENT_ID`
- `KEYCLOAK_OAUTH_AUDIENCE`
- `KEYCLOAK_OAUTH_SCOPES`
- `MCP_PORT`, `MCP_TRANSPORT_PROTOCOL`, `MCP_HOST_ENDPOINT`
- `ENABLE_AUTH`
- Proxy settings (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`)

### Deployment spec

- Remove `strategy: Recreate` (no PVC, can use `RollingUpdate`)
- Can scale `replicas` beyond 1
- Remove `volumeMounts` and `volumes` sections

## stdio Mode

No changes needed. `LocalLogin` already authenticates locally via browser and passes the token directly. It never uses the `OAuthProxy` or PVC.

## Migration Steps

1. Verify `KeycloakAuthProvider` works with RHBK 26.6.0 locally (stdio and HTTP)
2. Configure the `keycloak-mcp` client in EmployeeIDP as a public client (if not already)
3. Implement code changes (auth.py, service.py, settings.py, api.py)
4. Update tests
5. Deploy to stage with new ConfigMap (no PVC, no signing key secret)
6. Verify Claude Code can authenticate and call tools
7. Delete the PVC and signing key secret from the namespace
8. Update prod overlay when stage is validated

## Risk

| Risk | Mitigation |
|------|------------|
| FastMCP `KeycloakAuthProvider` bug with RHBK | Test locally first; fall back to custom `RemoteAuthProvider` subclass |
| Claude Code doesn't support direct Keycloak auth | It does — uses RFC 9728 discovery, confirmed in testing |
| Existing sessions break on deploy | Stateful sessions are ephemeral; users just re-authenticate |
| `KEYCLOAK_TRUSTED_EXCHANGERS` regression | Keep `KeycloakTokenVerifier`; compose with `MultiAuth` |

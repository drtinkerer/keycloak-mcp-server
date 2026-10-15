"""Stateless auth: Keycloak is the authorization server, the MCP server only validates."""

import ssl
import time

import httpx2
from fastmcp.server.auth import AccessToken, MultiAuth
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

from keycloak_mcp_server.src.settings import Settings


class KeycloakTokenVerifier(JWTVerifier):
    """Accept signed Keycloak user access tokens from an allowed authorized party."""

    def __init__(self, *, allowed_azp: frozenset[str], **kwargs):
        super().__init__(**kwargs)
        self.allowed_azp = allowed_azp

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await super().verify_token(token)
        if verified is None:
            return None
        claims = verified.claims
        now = time.time()
        if (
            claims.get("azp") not in self.allowed_azp
            or claims.get("typ") != "Bearer"
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
            or not isinstance(claims.get("exp"), (int, float))
            or claims["exp"] <= now
        ):
            return None
        nbf = claims.get("nbf")
        if nbf is not None and (not isinstance(nbf, (int, float)) or nbf > now):
            return None
        return verified


class KeycloakMultiAuth(MultiAuth):
    """The primary auth provider plus direct verification of token-exchanged tokens."""

    def __init__(self, *, provider: KeycloakAuthProvider, exchange: KeycloakTokenVerifier):
        super().__init__(
            server=provider,
            server_source_id="keycloak-auth-provider",
            verifiers={"keycloak-token-exchange": exchange},
            required_scopes=[],
        )
        self._provider = provider
        self._exchange_verifier = exchange

    async def aclose(self) -> None:
        if hasattr(self._exchange_verifier, "_http_client"):
            await self._exchange_verifier._http_client.aclose()


def create_keycloak_auth(
    config: Settings,
) -> KeycloakAuthProvider | KeycloakMultiAuth | None:
    """Build stateless auth for a configured Keycloak realm.

    The MCP server is a resource server only: it publishes RFC 9728 protected
    resource metadata pointing to Keycloak as the authorization server, and
    validates Keycloak-issued JWTs via JWKS. No tokens are stored server-side.
    """
    if not config.ENABLE_AUTH:
        return None
    required = ("KEYCLOAK_SERVER_URL", "KEYCLOAK_REALM", "KEYCLOAK_OAUTH_CLIENT_ID")
    missing = [name for name in required if not getattr(config, name)]
    if missing:
        raise ValueError("Keycloak auth requires: " + ", ".join(missing))
    if config.KEYCLOAK_DEV_ACCESS_TOKEN:
        raise ValueError(
            "Remove KEYCLOAK_DEV_ACCESS_TOKEN when enabling Keycloak auth."
        )

    upstream = str(config.KEYCLOAK_SERVER_URL).rstrip("/")
    realm_url = f"{upstream}/realms/{config.KEYCLOAK_REALM}"
    base = config.MCP_HOST_ENDPOINT.rstrip("/")

    exchangers = frozenset(config.KEYCLOAK_TRUSTED_EXCHANGERS)
    if any(not exchanger or exchanger.strip() != exchanger for exchanger in exchangers):
        raise ValueError("KEYCLOAK_TRUSTED_EXCHANGERS entries must be client IDs.")
    if config.KEYCLOAK_OAUTH_CLIENT_ID in exchangers:
        raise ValueError(
            "KEYCLOAK_TRUSTED_EXCHANGERS must not include KEYCLOAK_OAUTH_CLIENT_ID."
        )

    provider = KeycloakAuthProvider(
        realm_url=realm_url,
        base_url=base,
        audience=config.KEYCLOAK_OAUTH_AUDIENCE,
    )

    if not exchangers:
        return provider

    if config.KEYCLOAK_CA_BUNDLE:
        verify = ssl.create_default_context(cafile=config.KEYCLOAK_CA_BUNDLE)
    else:
        verify = ssl.create_default_context()
    verification_client = httpx2.AsyncClient(
        verify=verify, timeout=config.KEYCLOAK_TIMEOUT
    )
    oidc = f"{realm_url}/protocol/openid-connect"
    exchange = KeycloakTokenVerifier(
        allowed_azp=exchangers,
        jwks_uri=f"{oidc}/certs",
        issuer=realm_url,
        audience=config.KEYCLOAK_OAUTH_AUDIENCE,
        algorithm="RS256",
        http_client=verification_client,
    )
    return KeycloakMultiAuth(provider=provider, exchange=exchange)

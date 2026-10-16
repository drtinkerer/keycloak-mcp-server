"""Stateless auth: Keycloak signs users in; this server only validates their tokens.

The MCP server is an OAuth resource server. It publishes RFC 9728 protected
resource metadata naming the Keycloak realm as the authorization server, so MCP
clients sign the user in to Keycloak directly (browser, PKCE) and send the
Keycloak access token. Tokens are checked against the realm's JWKS on every
request; nothing is stored, so any number of replicas can serve the same users.
"""

import ssl
import time
from urllib.parse import quote, urlparse

import httpx2
from fastmcp.server.auth import AccessToken, MultiAuth
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

from keycloak_mcp_server.src.settings import Settings

LOCALHOST = {"localhost", "127.0.0.1", "::1"}


class KeycloakTokenVerifier(JWTVerifier):
    """Accept signed Keycloak user access tokens from an allowed authorized party."""

    def __init__(self, *, allowed_azp: frozenset[str], **kwargs):
        """Configure JWT verification and the clients allowed as `azp`.

        `azp` is the client that requested the token. For browser sign-in it
        is the client MCP clients sign in with (KEYCLOAK_OAUTH_CLIENT_ID).
        After an RFC 8693 token exchange it is the exchanging client (e.g. a
        gateway acting on behalf of the user), not the audience the exchange
        targeted.
        """
        super().__init__(**kwargs)
        self.allowed_azp = allowed_azp

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate the JWT and require the Keycloak user access-token claims."""
        verified = await super().verify_token(token)
        if verified is None:
            return None
        claims = verified.claims
        now = time.time()
        # JWTVerifier checks signature, issuer, audience and scopes. Require the
        # Keycloak access-token claims too; an ID token is never an Admin API token,
        # and a token from any other client of the realm is not accepted.
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
        verified.claims["keycloak_admin_token"] = True
        return verified


class KeycloakResourceServer(KeycloakAuthProvider):
    """Keycloak as the authorization server, with a CA-aware JWKS client."""

    def __init__(self, *, verification_client: httpx2.AsyncClient, **kwargs):
        """Keep the shared JWKS HTTP client so it can be closed on shutdown."""
        super().__init__(**kwargs)
        self.verification_client = verification_client

    async def aclose(self) -> None:
        """Release the HTTP connection pool used for JWKS verification."""
        await self.verification_client.aclose()


class KeycloakMultiAuth(MultiAuth):
    """Browser sign-in tokens plus directly verified token-exchanged tokens."""

    def __init__(
        self, *, provider: KeycloakResourceServer, exchange: KeycloakTokenVerifier
    ):
        """Try sign-in tokens first, then tokens exchanged by a trusted gateway."""
        super().__init__(
            server=provider,
            server_source_id="keycloak-sign-in",
            verifiers={"keycloak-token-exchange": exchange},
            # The sign-in verifier requires `openid` itself. An exchanged token
            # carries the scopes requested in the exchange instead, so the
            # shared request-level check is cleared.
            required_scopes=[],
        )
        self.provider = provider

    async def aclose(self) -> None:
        """Release the shared JWKS HTTP client."""
        await self.provider.aclose()


def create_keycloak_auth(
    config: Settings,
) -> KeycloakResourceServer | KeycloakMultiAuth | None:
    """Build stateless auth for a configured Keycloak realm; disabled auth has no routes.

    MCP clients sign in to Keycloak with KEYCLOAK_OAUTH_CLIENT_ID. When
    KEYCLOAK_TRUSTED_EXCHANGERS is set, Keycloak access tokens that one of
    those clients obtained by RFC 8693 token exchange for this server's
    audience are also accepted.
    """
    if not config.ENABLE_AUTH:
        return None
    required = ("KEYCLOAK_SERVER_URL", "KEYCLOAK_REALM", "KEYCLOAK_OAUTH_CLIENT_ID")
    missing = [name for name in required if not getattr(config, name)]
    if missing:
        raise ValueError("Keycloak sign-in requires: " + ", ".join(missing))
    if config.KEYCLOAK_DEV_ACCESS_TOKEN:
        raise ValueError(
            "Remove KEYCLOAK_DEV_ACCESS_TOKEN when enabling Keycloak sign-in."
        )
    if not config.KEYCLOAK_OAUTH_AUDIENCE:
        # Without an audience any token of the realm would be accepted.
        raise ValueError("Keycloak sign-in requires KEYCLOAK_OAUTH_AUDIENCE.")
    base = config.MCP_HOST_ENDPOINT.rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme == "http" and parsed.hostname not in LOCALHOST)
    ):
        raise ValueError(
            "MCP_HOST_ENDPOINT must be an HTTPS origin, or HTTP localhost for development."
        )
    upstream = str(config.KEYCLOAK_SERVER_URL).rstrip("/")
    target = urlparse(upstream)
    if target.scheme != "https" and target.hostname not in LOCALHOST:
        raise ValueError(
            "KEYCLOAK_SERVER_URL must use HTTPS except for a localhost test instance."
        )
    exchangers = frozenset(config.KEYCLOAK_TRUSTED_EXCHANGERS)
    if any(not exchanger or exchanger.strip() != exchanger for exchanger in exchangers):
        raise ValueError("KEYCLOAK_TRUSTED_EXCHANGERS entries must be client IDs.")
    if config.KEYCLOAK_OAUTH_CLIENT_ID in exchangers:
        # Keep the two paths apart: sign-in tokens must carry `openid`.
        raise ValueError(
            "KEYCLOAK_TRUSTED_EXCHANGERS must not include KEYCLOAK_OAUTH_CLIENT_ID."
        )
    issuer = upstream + "/realms/" + quote(config.KEYCLOAK_REALM, safe="")
    jwks_uri = issuer + "/protocol/openid-connect/certs"
    # The built-in verifier would fetch JWKS with default trust; share one
    # client that honours KEYCLOAK_CA_BUNDLE across both paths instead.
    verify = ssl.create_default_context(cafile=config.KEYCLOAK_CA_BUNDLE)
    verification_client = httpx2.AsyncClient(
        verify=verify, timeout=config.KEYCLOAK_TIMEOUT
    )
    provider = KeycloakResourceServer(
        realm_url=issuer,
        base_url=base,
        token_verifier=KeycloakTokenVerifier(
            allowed_azp=frozenset({config.KEYCLOAK_OAUTH_CLIENT_ID}),
            jwks_uri=jwks_uri,
            issuer=issuer,
            audience=config.KEYCLOAK_OAUTH_AUDIENCE,
            algorithm="RS256",
            required_scopes=["openid"],
            http_client=verification_client,
        ),
        verification_client=verification_client,
    )
    if not exchangers:
        return provider
    exchange = KeycloakTokenVerifier(
        allowed_azp=exchangers,
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=config.KEYCLOAK_OAUTH_AUDIENCE,
        algorithm="RS256",
        http_client=verification_client,
    )
    return KeycloakMultiAuth(provider=provider, exchange=exchange)

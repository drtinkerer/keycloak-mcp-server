"""Browser OAuth for MCP clients, with separate MCP and Keycloak user tokens."""

import ssl
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken, MultiAuth, OAuthProxy
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.providers.jwt import JWTVerifier
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from keycloak_mcp_server.src.settings import Settings


class KeycloakTokenVerifier(JWTVerifier):
    """Accept signed Keycloak user access tokens from an allowed authorized party."""

    def __init__(self, *, allowed_azp: frozenset[str], **kwargs):
        """Configure JWT verification and the clients allowed as `azp`.

        `azp` is the client that requested the token. For browser sign-in it
        is this server's own Keycloak client. After an RFC 8693 token
        exchange it is the exchanging client (e.g. a gateway acting on behalf
        of the user), not the audience the exchange targeted.
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
        # Keycloak access-token claims too; an ID token is never an Admin API token.
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


class KeycloakOAuthProxy(OAuthProxy):
    """Use the same trusted CA and timeout for OAuth and Admin API requests."""

    def __init__(
        self,
        *,
        verify: ssl.SSLContext,
        timeout: int,
        verification_client: httpx2.AsyncClient,
        **kwargs,
    ):
        """Configure shared TLS trust and the upstream token-verification client."""
        self.keycloak_verify = verify
        self.keycloak_timeout = timeout
        self.verification_client = verification_client
        super().__init__(**kwargs)

    def _create_upstream_oauth_client(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self._upstream_client_id,
            client_secret=(
                self._upstream_client_secret.get_secret_value()
                if self._upstream_client_secret is not None
                else None
            ),
            token_endpoint_auth_method=(
                "client_secret_basic" if self._upstream_client_secret else "none"
            ),
            timeout=self.keycloak_timeout,
            verify=self.keycloak_verify,
        )

    async def aclose(self) -> None:
        """Release the HTTP connection pool used for JWKS verification."""
        await self.verification_client.aclose()


class KeycloakMultiAuth(MultiAuth):
    """The OAuth proxy plus direct verification of token-exchanged tokens."""

    def __init__(self, *, proxy: KeycloakOAuthProxy, exchange: KeycloakTokenVerifier):
        """Try the proxy's own tokens first, then exchanged Keycloak tokens."""
        super().__init__(
            server=proxy,
            server_source_id="keycloak-oauth-proxy",
            verifiers={"keycloak-token-exchange": exchange},
            # The proxy path already requires `openid` in its upstream token
            # check. An exchanged token carries the scopes requested in the
            # exchange instead, so the shared request-level check is cleared.
            required_scopes=[],
        )
        self.proxy = proxy

    async def aclose(self) -> None:
        """Release the shared JWKS HTTP client."""
        await self.proxy.aclose()


def create_keycloak_auth(
    config: Settings,
) -> KeycloakOAuthProxy | KeycloakMultiAuth | None:
    """Build auth for a configured Keycloak realm; disabled auth has no routes.

    Browser sign-in always goes through the OAuth proxy. When
    KEYCLOAK_TRUSTED_EXCHANGERS is set, Keycloak access tokens that one of
    those clients obtained by RFC 8693 token exchange for this server's
    audience are also accepted directly.
    """
    if not config.ENABLE_AUTH:
        return None
    required = (
        "KEYCLOAK_SERVER_URL",
        "KEYCLOAK_REALM",
        "KEYCLOAK_OAUTH_CLIENT_ID",
        "KEYCLOAK_OAUTH_SIGNING_KEY",
    )
    missing = [name for name in required if not getattr(config, name)]
    if missing:
        raise ValueError("Browser OAuth requires: " + ", ".join(missing))
    assert config.KEYCLOAK_OAUTH_SIGNING_KEY is not None
    if config.KEYCLOAK_DEV_ACCESS_TOKEN:
        raise ValueError(
            "Remove KEYCLOAK_DEV_ACCESS_TOKEN when enabling browser OAuth."
        )
    signing_key = config.KEYCLOAK_OAUTH_SIGNING_KEY.get_secret_value()
    if len(signing_key) < 32:
        raise ValueError(
            "KEYCLOAK_OAUTH_SIGNING_KEY must contain at least 32 characters."
        )
    scopes = config.KEYCLOAK_OAUTH_SCOPES.split()
    if "openid" not in scopes or not config.KEYCLOAK_OAUTH_AUDIENCE:
        raise ValueError(
            "Browser OAuth requires the openid scope and KEYCLOAK_OAUTH_AUDIENCE."
        )
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
        or (
            parsed.scheme == "http"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        raise ValueError(
            "MCP_HOST_ENDPOINT must be an HTTPS origin, or HTTP localhost for development."
        )
    upstream = str(config.KEYCLOAK_SERVER_URL).rstrip("/")
    target = urlparse(upstream)
    if target.scheme != "https" and target.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError(
            "KEYCLOAK_SERVER_URL must use HTTPS except for a localhost test instance."
        )
    if not config.KEYCLOAK_OAUTH_CLIENT_REDIRECT_URIS:
        raise ValueError("Configure at least one allowed MCP client redirect URI.")
    exchangers = frozenset(config.KEYCLOAK_TRUSTED_EXCHANGERS)
    if any(not exchanger or exchanger.strip() != exchanger for exchanger in exchangers):
        raise ValueError("KEYCLOAK_TRUSTED_EXCHANGERS entries must be client IDs.")
    if config.KEYCLOAK_OAUTH_CLIENT_ID in exchangers:
        # Otherwise a plain sign-in token for this client could skip the proxy.
        raise ValueError(
            "KEYCLOAK_TRUSTED_EXCHANGERS must not include KEYCLOAK_OAUTH_CLIENT_ID."
        )
    issuer = upstream + "/realms/" + quote(config.KEYCLOAK_REALM, safe="")
    oidc = issuer + "/protocol/openid-connect"
    verify = ssl.create_default_context(cafile=config.KEYCLOAK_CA_BUNDLE)
    verification_client = httpx2.AsyncClient(
        verify=verify, timeout=config.KEYCLOAK_TIMEOUT
    )
    verifier = KeycloakTokenVerifier(
        allowed_azp=frozenset({config.KEYCLOAK_OAUTH_CLIENT_ID}),
        jwks_uri=oidc + "/certs",
        issuer=issuer,
        audience=config.KEYCLOAK_OAUTH_AUDIENCE,
        algorithm="RS256",
        required_scopes=["openid"],
        http_client=verification_client,
    )
    directory = Path(config.KEYCLOAK_OAUTH_STORAGE_DIR).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    encryption_key = derive_jwt_key(
        high_entropy_material=signing_key,
        salt=f"keycloak-oauth-storage:{base}:{config.KEYCLOAK_OAUTH_CLIENT_ID}",
    )
    storage = FernetEncryptionWrapper(
        key_value=FileTreeStore(data_directory=directory),
        fernet=Fernet(encryption_key),
    )
    proxy = KeycloakOAuthProxy(
        upstream_authorization_endpoint=oidc + "/auth",
        upstream_token_endpoint=oidc + "/token",
        upstream_client_id=config.KEYCLOAK_OAUTH_CLIENT_ID,
        upstream_client_secret=(
            config.KEYCLOAK_OAUTH_CLIENT_SECRET.get_secret_value()
            if config.KEYCLOAK_OAUTH_CLIENT_SECRET
            else None
        ),
        token_verifier=verifier,
        base_url=base,
        redirect_path="/auth/callback",
        allowed_client_redirect_uris=config.KEYCLOAK_OAUTH_CLIENT_REDIRECT_URIS,
        valid_scopes=scopes,
        forward_pkce=True,
        # Keycloak uses configured audience mappers. The local MCP resource URL
        # is validated by the proxy and is not an upstream Keycloak resource.
        forward_resource=False,
        token_endpoint_auth_method=(
            "client_secret_basic" if config.KEYCLOAK_OAUTH_CLIENT_SECRET else "none"
        ),
        client_storage=storage,
        jwt_signing_key=signing_key,
        require_authorization_consent=True,
        enable_cimd=False,
        verify=verify,
        timeout=config.KEYCLOAK_TIMEOUT,
        verification_client=verification_client,
    )
    if not exchangers:
        return proxy
    exchange = KeycloakTokenVerifier(
        allowed_azp=exchangers,
        jwks_uri=oidc + "/certs",
        issuer=issuer,
        audience=config.KEYCLOAK_OAUTH_AUDIENCE,
        algorithm="RS256",
        http_client=verification_client,
    )
    return KeycloakMultiAuth(proxy=proxy, exchange=exchange)

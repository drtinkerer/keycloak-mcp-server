"""Browser sign-in for stdio mode: the server runs as one local user.

In stdio mode there is no HTTP endpoint for the MCP client's OAuth flow, so the
server signs the user in itself, as a native app (RFC 8252): authorization code
with PKCE, a random state, and a loopback callback on 127.0.0.1. Tokens are
cached in a file readable only by the user, and refreshed before they expire.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import ssl
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from fastmcp.exceptions import ToolError

from keycloak_mcp_server.src.settings import Settings
from keycloak_mcp_server.utils.pylogger import get_python_logger

logger = get_python_logger()

LOGIN_TIMEOUT_SECONDS = 300
# Refresh this long before expiry so a token never lapses mid-request.
EXPIRY_MARGIN_SECONDS = 30

PAGE = """<!doctype html><meta charset="utf-8"><title>Keycloak MCP</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto">
<h2>{title}</h2><p>{detail}</p></body>"""


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class LocalLogin:
    """Hold one user's Keycloak tokens for a locally running stdio server."""

    def __init__(self, config: Settings):
        """Derive endpoints and the cache location from the server settings."""
        if not config.KEYCLOAK_SERVER_URL or not config.KEYCLOAK_REALM:
            raise ToolError(
                "Configure KEYCLOAK_SERVER_URL and KEYCLOAK_REALM before calling Keycloak tools."
            )
        if not config.KEYCLOAK_OAUTH_CLIENT_ID:
            raise ToolError("Configure KEYCLOAK_OAUTH_CLIENT_ID for stdio sign-in.")
        server = str(config.KEYCLOAK_SERVER_URL).rstrip("/")
        realm = quote(config.KEYCLOAK_REALM, safe="")
        oidc = f"{server}/realms/{realm}/protocol/openid-connect"
        self.authorize_url = f"{oidc}/auth"
        self.token_url = f"{oidc}/token"
        self.client_id = config.KEYCLOAK_OAUTH_CLIENT_ID
        self.scopes = config.KEYCLOAK_OAUTH_SCOPES
        self.port = config.KEYCLOAK_LOGIN_PORT
        self.redirect_uri = f"http://127.0.0.1:{self.port}/callback"
        self.verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=config.KEYCLOAK_CA_BUNDLE)
            if config.KEYCLOAK_CA_BUNDLE
            else True
        )
        self.timeout = config.KEYCLOAK_TIMEOUT
        directory = Path(config.KEYCLOAK_TOKEN_CACHE_DIR).expanduser()
        self.cache_file = directory / "tokens.json"
        self.cache_key = f"{server}|{config.KEYCLOAK_REALM}|{self.client_id}"
        self._lock = asyncio.Lock()
        self._pending: asyncio.Task | None = None
        self._tokens: dict[str, Any] | None = self._load()

    async def access_token(self) -> str:
        """Return a valid access token, refreshing or signing in as needed."""
        async with self._lock:
            tokens = self._tokens
            if (
                tokens
                and tokens.get("expires_at", 0) > time.time() + EXPIRY_MARGIN_SECONDS
            ):
                return tokens["access_token"]
            if tokens and tokens.get("refresh_token"):
                refreshed = await self._refresh(tokens["refresh_token"])
                if refreshed:
                    return refreshed["access_token"]
            return (await self._sign_in())["access_token"]

    def invalidate_access_token(self) -> None:
        """Force a refresh on the next call, e.g. after Keycloak rejected it."""
        if self._tokens:
            self._tokens["expires_at"] = 0

    async def _refresh(self, refresh_token: str) -> dict[str, Any] | None:
        response = await self._post_token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        if response is None:
            logger.info("Keycloak refresh token rejected; signing in again")
            self._tokens = None
            self._save()
        return response

    async def _sign_in(self) -> dict[str, Any]:
        # A sign-in already waiting in the browser is reused, not restarted.
        if self._pending is None or self._pending.done():
            self._pending = asyncio.create_task(self._browser_flow())
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._pending), LOGIN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            raise ToolError(
                "Keycloak sign-in was not completed within 5 minutes. Call the tool again to retry."
            ) from None

    async def _browser_flow(self) -> dict[str, Any]:
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        code_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                request_line = (await reader.readline()).decode(errors="replace")
                # Drain headers; nothing in them is used.
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                parts = request_line.split(" ")
                target = urlparse(parts[1] if len(parts) > 1 else "/")
                if target.path != "/callback":
                    status, title, detail = "404 Not Found", "Not found", ""
                else:
                    query = parse_qs(target.query)
                    status, title, detail = "400 Bad Request", "Sign-in failed", ""
                    if query.get("state", [""])[0] != state:
                        detail = "The response did not match this sign-in. Try again."
                    elif "error" in query:
                        detail = "Keycloak returned: " + query["error"][0]
                    elif "code" in query and not code_future.done():
                        code_future.set_result(query["code"][0])
                        status, title = "200 OK", "Signed in"
                        detail = "You can close this tab and return to your MCP client."
                    else:
                        detail = "Missing authorization code."
                body = PAGE.format(title=title, detail=detail).encode()
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
            finally:
                writer.close()

        try:
            server = await asyncio.start_server(handle, "127.0.0.1", self.port)
        except OSError as error:
            raise ToolError(
                f"Cannot listen on 127.0.0.1:{self.port} for the sign-in callback "
                "(is another sign-in running?). Set KEYCLOAK_LOGIN_PORT to a free port "
                "and register its /callback URI on the Keycloak client."
            ) from error
        url = (
            self.authorize_url
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect_uri,
                    "scope": self.scopes,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        async with server:
            # stdout carries the MCP protocol; the URL is logged to stderr only.
            logger.info("Opening Keycloak sign-in in the browser", url=url)
            if not await asyncio.to_thread(webbrowser.open, url):
                logger.warning(
                    "Could not open a browser; open the URL manually", url=url
                )
            code = await code_future
        tokens = await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            }
        )
        if tokens is None:
            raise ToolError(
                "Keycloak rejected the sign-in. Call the tool again to retry."
            )
        return tokens

    async def _post_token(self, form: dict[str, str]) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self.timeout
            ) as client:
                response = await client.post(
                    self.token_url, data={**form, "client_id": self.client_id}
                )
        except httpx.HTTPError:
            raise ToolError(
                "Cannot reach Keycloak to sign in. Check KEYCLOAK_SERVER_URL and connectivity."
            ) from None
        if response.status_code != 200:
            return None
        payload = response.json()
        if not isinstance(payload.get("access_token"), str):
            return None
        tokens = {
            "access_token": payload["access_token"],
            # Keep the current refresh token if Keycloak does not rotate it.
            "refresh_token": payload.get("refresh_token") or form.get("refresh_token"),
            "expires_at": time.time() + int(payload.get("expires_in", 60)),
        }
        self._tokens = tokens
        self._save()
        return tokens

    def _load(self) -> dict[str, Any] | None:
        try:
            cache = json.loads(self.cache_file.read_text())
        except (OSError, ValueError):
            return None
        entry = cache.get(self.cache_key) if isinstance(cache, dict) else None
        return entry if isinstance(entry, dict) else None

    def _save(self) -> None:
        try:
            cache = json.loads(self.cache_file.read_text())
            if not isinstance(cache, dict):
                cache = {}
        except (OSError, ValueError):
            cache = {}
        if self._tokens is None:
            cache.pop(self.cache_key, None)
        else:
            cache[self.cache_key] = self._tokens
        self.cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create the file 0600 before writing so tokens are never world-readable.
        descriptor = os.open(
            self.cache_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "w") as handle:
            json.dump(cache, handle)
        os.chmod(self.cache_file, 0o600)

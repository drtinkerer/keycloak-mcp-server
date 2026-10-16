"""FastAPI host for the Keycloak MCP server with stateless Keycloak auth."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from keycloak_mcp_server import __version__
from keycloak_mcp_server.src.keycloak_api.auth import create_keycloak_auth
from keycloak_mcp_server.src.mcp import KeycloakMCPServer
from keycloak_mcp_server.src.settings import Settings, settings


def create_app(config: Settings) -> FastAPI:
    """Build one application with matching MCP, auth, and lifecycle settings."""
    auth = create_keycloak_auth(config)
    server = KeycloakMCPServer(auth=auth)
    if config.MCP_TRANSPORT_PROTOCOL == "sse":
        mcp_app = server.mcp.http_app(transport="sse", path="/sse")
    else:
        mcp_app = server.mcp.http_app(path="/mcp")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            if auth is not None:
                await auth.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.mcp_server = server
    app.state.keycloak_auth = auth

    @app.get("/health")
    async def health_check():
        return JSONResponse(
            content={
                "status": "healthy",
                "service": "keycloak-mcp-server",
                "transport_protocol": config.MCP_TRANSPORT_PROTOCOL,
                "version": __version__,
                "authentication": "keycloak" if auth else "disabled",
            }
        )

    app.mount("/", mcp_app)
    if config.CORS_ENABLED:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.CORS_ORIGINS,
            allow_credentials=config.CORS_CREDENTIALS,
            allow_methods=config.CORS_METHODS,
            allow_headers=config.CORS_HEADERS,
        )
    return app


app = create_app(settings)
server = app.state.mcp_server

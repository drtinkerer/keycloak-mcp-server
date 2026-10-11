"""Keycloak MCP Server implementation.

This module contains the main Keycloak MCP Server class that provides
tools for MCP clients. It uses FastMCP to register and manage MCP capabilities.
"""

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from keycloak_mcp_server.src.settings import settings
from keycloak_mcp_server.src.tools.keycloak_tools import KEYCLOAK_TOOLS
from keycloak_mcp_server.utils.pylogger import (
    force_reconfigure_all_loggers,
    get_python_logger,
)

logger = get_python_logger()


class KeycloakMCPServer:
    """Main Keycloak MCP Server implementation following tools-first architecture.

    This server provides only tools, not resources or prompts, adhering to
    the tools-first architectural pattern for MCP servers.
    """

    def __init__(self, auth: AuthProvider | None = None):
        """Initialize the MCP server with read-only Keycloak tools."""
        try:
            # Initialize FastMCP server
            self.mcp = FastMCP("keycloak", auth=auth)

            # Force reconfigure all loggers after FastMCP initialization to ensure structured logging
            force_reconfigure_all_loggers(settings.PYTHON_LOG_LEVEL)

            self._register_mcp_tools()

            logger.info("Keycloak MCP Server initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Keycloak MCP Server: {e}")
            raise

    def _register_mcp_tools(self) -> None:
        """Register only the read-only Keycloak Admin API tools."""
        for tool in KEYCLOAK_TOOLS:
            self.mcp.tool(
                annotations={
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                }
            )(tool)

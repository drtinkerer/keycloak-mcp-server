"""Keycloak MCP Server implementation.

This module contains the main Keycloak MCP Server class that provides
tools for MCP clients. It uses FastMCP to register and manage MCP capabilities.
"""

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from keycloak_mcp_server.src.settings import settings
from keycloak_mcp_server.src.tools.keycloak_tools import (
    find_users_with_role,
    get_client,
    get_group_role_mappings,
    get_ldap_federation,
    get_realm_info,
    get_user,
    get_user_groups,
    get_user_role_mappings,
    list_admin_events,
    list_client_protocol_mappers,
    list_client_scopes,
    list_client_sessions,
    list_clients,
    list_events,
    list_group_members,
    list_groups,
    list_realm_roles,
    list_user_sessions,
    preview_access_token,
    search_users,
)
from keycloak_mcp_server.utils.pylogger import (
    force_reconfigure_all_loggers,
    get_python_logger,
)

logger = get_python_logger()

# Every tool only reads from the Keycloak Admin API.
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


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
        """Register the read-only Keycloak Admin API tools.

        Listed one by one rather than in a loop so that tools stay visible to
        static analysis (registries and scanners read them from the source).
        tests/test_keycloak.py checks this list matches KEYCLOAK_TOOLS.
        """
        self.mcp.tool(get_realm_info, annotations=READ_ONLY)
        self.mcp.tool(search_users, annotations=READ_ONLY)
        self.mcp.tool(get_user, annotations=READ_ONLY)
        self.mcp.tool(get_user_groups, annotations=READ_ONLY)
        self.mcp.tool(list_groups, annotations=READ_ONLY)
        self.mcp.tool(list_clients, annotations=READ_ONLY)
        self.mcp.tool(list_realm_roles, annotations=READ_ONLY)
        self.mcp.tool(get_client, annotations=READ_ONLY)
        self.mcp.tool(list_client_protocol_mappers, annotations=READ_ONLY)
        self.mcp.tool(list_client_scopes, annotations=READ_ONLY)
        self.mcp.tool(get_user_role_mappings, annotations=READ_ONLY)
        self.mcp.tool(get_group_role_mappings, annotations=READ_ONLY)
        self.mcp.tool(list_group_members, annotations=READ_ONLY)
        self.mcp.tool(find_users_with_role, annotations=READ_ONLY)
        self.mcp.tool(list_events, annotations=READ_ONLY)
        self.mcp.tool(list_admin_events, annotations=READ_ONLY)
        self.mcp.tool(list_user_sessions, annotations=READ_ONLY)
        self.mcp.tool(list_client_sessions, annotations=READ_ONLY)
        self.mcp.tool(preview_access_token, annotations=READ_ONLY)
        self.mcp.tool(get_ldap_federation, annotations=READ_ONLY)

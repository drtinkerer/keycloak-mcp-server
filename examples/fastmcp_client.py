#!/usr/bin/env python3
"""MCP Server Demo - Using FastMCP Client.

This example demonstrates how to connect to the running Keycloak MCP server
using the FastMCP Client and discover the available read-only Keycloak tools.

Prerequisites:
- Keycloak MCP server must be running on http://localhost:5001
- Install dependencies: uv sync --locked
"""

import asyncio

import requests
from fastmcp import Client


class FastMCPClient:
    """Demo client for the Keycloak MCP server using FastMCP Client."""

    def __init__(self, server_url: str):
        """Initialize the MCP client demo.

        Args:
            server_url: URL of the MCP server
        """
        self.server_url = server_url
        self.health_endpoint = f"{server_url}/health"

        # MCP configuration
        self.config = {
            "mcpServers": {"keycloak_mcp_server": {"url": f"{server_url}/mcp"}}
        }

    def check_server_health(self):
        """Check if the MCP server is healthy."""
        try:
            response = requests.get(self.health_endpoint, timeout=10)
            if response.status_code == 200:
                health_data = response.json()
                print("Server is healthy")
                print(f"   Service: {health_data.get('service')}")
                print(f"   Transport Protocol: {health_data.get('transport_protocol')}")
                print(f"   Version: {health_data.get('version')}")
                return True
            else:
                print(f"Health check failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"Error connecting to MCP server: {e}")
            return False

    async def demonstrate_tools(self, client):
        """Discover all available tools using an actual MCP connection."""
        print("\n" + "=" * 60)
        print("Available MCP Tools")
        print("=" * 60)

        try:
            # List available tools
            tools = await client.list_tools()
            print(f"Found {len(tools)} tool(s):")
            for tool in tools:
                print(f"   - {tool.name}: {tool.description}")

            print("\nTo call these tools, configure Keycloak and run:")
            print("   uv run python examples/keycloak_client.py")

        except Exception as e:
            print(f"   Error accessing tools: {e}")

    async def run_demo(self):
        """Run the complete demo using FastMCP Client."""
        print("Keycloak MCP Server Demo with FastMCP Client")
        print("=" * 60)

        # Check server health
        if not self.check_server_health():
            print("Cannot proceed without a healthy MCP server")
            return

        # Create MCP client
        client = Client(self.config)

        try:
            async with client:
                print("Connected to MCP server")

                # Demonstrate all tools
                await self.demonstrate_tools(client)

                print("\nDemo completed successfully!")

        except Exception as e:
            print(f"Error during demo: {e}")


async def main():
    """Main function to run the demo."""
    # Test MCP server deployed locally
    demo = FastMCPClient(server_url="http://localhost:5001")

    # Test MCP server deployed on OpenShift
    # demo = FastMCPClient(server_url="https://your-mcp-server.apps.example.com")
    await demo.run_demo()


if __name__ == "__main__":
    asyncio.run(main())

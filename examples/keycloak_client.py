"""Discover and smoke-test read-only Keycloak tools using a direct MCP client."""

import argparse
import asyncio
import json
from typing import Any

from fastmcp import Client


async def run(
    url: str, list_only: bool, username: str | None, oauth: bool = False
) -> None:
    """Discover tools and optionally smoke-test them with the user’s identity."""
    async with Client(url, auth="oauth" if oauth else None) as client:
        tools = await client.list_tools()
        print("Available tools:")
        for tool in tools:
            print(f"  {tool.name}: {tool.description.splitlines()[0]}")
        if list_only:
            return

        async def call(name: str, arguments: dict):
            result = await client.call_tool(name, arguments)
            print(f"\n{name}:\n{json.dumps(result.data, indent=2)}")
            return result.data

        await call("get_realm_info", {})
        arguments: dict[str, Any] = {"max_results": 1}
        if username:
            arguments.update({"username": username, "exact": True})
        users = await call("search_users", arguments)
        if users["items"]:
            user_id = users["items"][0]["id"]
            await call("get_user", {"user_id": user_id})
            await call("get_user_groups", {"user_id": user_id, "max_results": 5})
        else:
            print("No matching users; skipping user detail and membership calls.")
        for name in ("list_groups", "list_clients", "list_realm_roles"):
            await call(name, {"max_results": 5})


def main() -> None:
    """Parse the direct client’s command-line options and run it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5001/mcp")
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Discover tools without calling the Keycloak Admin API",
    )
    parser.add_argument(
        "--username", help="Exact user to inspect instead of the first user"
    )
    parser.add_argument(
        "--oauth", action="store_true", help="Sign in through browser OAuth"
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, args.list_only, args.username, args.oauth))
    except Exception as error:
        parser.exit(1, f"Keycloak MCP test failed: {error}\n")


if __name__ == "__main__":
    main()

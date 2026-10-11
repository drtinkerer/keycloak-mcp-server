# MCP Client Examples

Ready-to-use client examples for connecting to your MCP server with different frameworks.

## 📁 **Client Examples**

- `fastmcp_client.py` - Health check and read-only Keycloak tool discovery
- `langgraph_client.py` - LangGraph integration with tool orchestration
- `keycloak_client.py` - Discover or smoke-test the read-only Keycloak tools

Discover the Keycloak tools with MCP authentication disabled:

```bash
uv run python examples/keycloak_client.py --list-only
```

After [configuring browser OAuth](../docs/authentication.md), use the direct
client to log in and test without an LLM:

```bash
uv run python examples/keycloak_client.py --oauth --username alice
```

The simple `fastmcp_client.py` and LangGraph examples assume MCP authentication
is disabled; use `keycloak_client.py --oauth`, Codex, or Claude for browser OAuth.

## 🚀 **Quick Start**

### **1. FastMCP Client (Simple)**
```bash
# Install dependencies
uv sync --locked

# Run the example
uv run python examples/fastmcp_client.py
```

**Use case**: Direct tool testing, simple integrations, debugging

### **2. LangGraph Client (Advanced)**
```bash
# Install dependencies
uv sync --locked

# Run the example
uv run python examples/langgraph_client.py
```

**Use case**: Complex workflows, agent orchestration, production systems

## 🔧 **Configuration**

**Update connection settings for your deployment:**

```python
# Both files - update these URLs
server_url = "http://localhost:5001"           # Local development
# server_url = "http://0.0.0.0:4001"          # Custom port
# server_url = "https://your-mcp.apps.cluster.com"  # Production OpenShift
```

## 📋 **What Each Example Shows**

### **FastMCP Client:**
- ✅ Basic server connection
- ✅ Tool discovery and listing
- ✅ Direct tool execution
- ✅ Error handling
- ✅ Response parsing

### **LangGraph Client:**
- ✅ Agent workflow orchestration
- ✅ Multi-tool coordination
- ✅ State management
- ✅ Complex business logic
- ✅ Production-ready patterns

## Calling an Keycloak tool

After configuring the Keycloak target and user token:

```python
result = await client.call_tool("search_users", {
    "username": "alice", "exact": True, "max_results": 5
})
```

The LangGraph example asks an LLM to inspect realm settings with `get_realm_info`.
It requires Keycloak configuration as well as Gemini credentials.

## 🔍 **Testing Your Server**

```bash
# Test server health first
curl http://localhost:5001/health

# Run client examples to verify tool integration
uv run python examples/fastmcp_client.py
uv run python examples/langgraph_client.py
```

## 📚 **Learn More**

- **FastMCP**: Simple, direct MCP connections
- **LangGraph**: Advanced agent workflows and orchestration
- **MCP Protocol**: https://modelcontextprotocol.io/

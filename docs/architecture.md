# Architecture

## System Architecture

```mermaid
graph TB
    subgraph "External Clients"
        A[Claude Code/LLM Client]
        B[Custom MCP Client]
        C[Development Tools]
    end

    subgraph "Network Layer"
        D[Load Balancer/Proxy]
        E[SSL Termination]
    end

    subgraph "Keycloak MCP Server"
        subgraph "Application Layer"
            F[FastAPI Application<br/>api.py]
            G[Health Check Endpoint<br/>/health]
            H[MCP Protocol Handler<br/>/mcp]
        end

        subgraph "MCP Core"
            I[KeycloakMCPServer<br/>mcp.py]
            J[FastMCP Instance<br/>Protocol Implementation]
            K[Tool Registry<br/>Dynamic Registration]
        end

        subgraph "Tool Layer"
            L[Realm Tools<br/>Settings and Roles]
            M[User Tools<br/>Search and Memberships]
            N[Directory Tools<br/>Groups and Clients]
            O[Custom Tools<br/>Extensible]
        end

        subgraph "Infrastructure Layer"
            P[Configuration Management<br/>settings.py]
            Q[Structured Logging<br/>pylogger.py]
            R[Error Handling<br/>Exception Management]
            S[Asset Management<br/>Static Resources]
        end

        subgraph "Transport Layer"
            T[HTTP Transport]
            U[SSE Transport]
            V[Streamable HTTP Transport]
        end
    end

    subgraph "External Dependencies"
        W[Environment Variables<br/>.env]
        X[SSL Certificates<br/>TLS/HTTPS]
        Y[Static Assets<br/>Images/Files]
        Z[Container Runtime<br/>Docker/Podman]
    end

    A --> D
    B --> D
    C --> D
    D --> E
    E --> F
    F --> G
    F --> H
    H --> I
    I --> J
    J --> K
    K --> L
    K --> M
    K --> N
    K --> O
    I --> P
    I --> Q
    I --> R
    M --> S
    F --> T
    F --> U
    F --> V
    P --> W
    E --> X
    S --> Y
    Z --> F

    classDef client fill:#e3f2fd
    classDef network fill:#f3e5f5
    classDef application fill:#e8f5e8
    classDef core fill:#fff3e0
    classDef tools fill:#fce4ec
    classDef infrastructure fill:#f1f8e9
    classDef transport fill:#fef7e0
    classDef external fill:#f5f5f5

    class A,B,C client
    class D,E network
    class F,G,H application
    class I,J,K core
    class L,M,N,O tools
    class P,Q,R,S infrastructure
    class T,U,V transport
    class W,X,Y,Z external
```

## Control Flow

```mermaid
flowchart TD
    A[MCP Client Request] --> B{Transport Protocol?}

    B -->|HTTP/Streamable-HTTP| C[FastAPI App<br/>api.py]
    B -->|SSE| D[SSE App<br/>FastMCP http_app]

    C --> E[Health Check?]
    D --> E

    E -->|/health| F[Health Endpoint<br/>Return Status]
    E -->|/mcp| G[MCP Request Handler<br/>FastMCP Instance]

    G --> H{MCP Method Type?}

    H -->|tools/list| I[List Available Tools<br/>Return tool definitions]
    H -->|tools/call| J[Tool Execution Router<br/>mcp.py]

    J --> K[Read-only Keycloak Tools<br/>keycloak_tools.py]
    K --> O[Validate Inputs<br/>IDs, filters, and page limits]
    O --> P[Resolve User Token<br/>keycloak_api/service.py]
    P --> Q[Admin REST API GET<br/>python-keycloak]
    Q --> R[Select Response Fields<br/>Omit credentials and secrets]
    R --> U[Return Bounded Results]

    U --> V[Return Success Response<br/>JSON format]

    V --> W[Send to MCP Client<br/>Complete request cycle]

    F --> W
    I --> W

    X[Configuration Loading<br/>settings.py] --> Y[Environment Variables<br/>.env file]
    Y --> Z[Pydantic Validation<br/>Type checking & defaults]
    Z --> AA[Server Startup<br/>main.py]
    AA --> C
    AA --> D

    BB[Error Handling] --> CC[Structured Logging<br/>pylogger.py]
    CC --> DD[JSON Output<br/>Timestamp + Context]

    O --> BB
    P --> BB
    Q --> BB

    classDef request fill:#e3f2fd
    classDef routing fill:#f3e5f5
    classDef tools fill:#e8f5e8
    classDef config fill:#fff3e0
    classDef logging fill:#fce4ec

    class A,B,E,H,K request
    class C,D,G,J routing
    class L,M,N,O,P,Q,R,S,T tools
    class X,Y,Z,AA config
    class BB,CC,DD logging
```

## Code Structure

```
keycloak-mcp-server/
├── keycloak_mcp_server/           # Main package directory
│   ├── __init__.py
│   ├── src/                       # Core source code
│   │   ├── __init__.py
│   │   ├── main.py               # Application entry point & startup logic
│   │   ├── api.py                # FastAPI application & transport setup
│   │   ├── mcp.py                # MCP server implementation & tool registration
│   │   ├── settings.py           # Pydantic-based configuration management
│   │   ├── assets/               # Static resource files (none yet)
│   │   ├── keycloak_api/         # Keycloak integration
│   │   │   ├── __init__.py
│   │   │   ├── auth.py           # Stateless Keycloak token verification
│   │   │   └── service.py        # Request-scoped Admin API connections
│   │   └── tools/                # MCP tool implementations
│   │       ├── __init__.py
│   │       └── keycloak_tools.py # Twenty read-only Keycloak tools
│   └── utils/                    # Shared utilities
│       ├── __init__.py
│       └── pylogger.py          # Structured logging with structlog
├── tests/                        # Comprehensive test suite
│   ├── conftest.py              # Pytest fixtures and configuration
│   ├── test_api.py              # API endpoint tests
│   ├── test_basic.py            # Basic integration tests
│   ├── test_main.py             # Entry point tests
│   ├── test_mcp.py              # MCP server tests
│   ├── test_settings.py         # Configuration tests
│   ├── test_keycloak.py         # Tools against simulated Admin API responses
│   ├── test_keycloak_oauth.py   # Signed-token auth against simulated Keycloak
│   └── test_utils.py            # Utility tests
├── examples/                     # Client examples
│   ├── fastmcp_client.py        # FastMCP client example
│   ├── keycloak_client.py       # Direct smoke test, with optional browser sign-in
│   └── langgraph_client.py      # LangGraph client example
├── deployment/                   # Deployment configurations
│   └── openshift/               # OpenShift manifests
├── .github/                      # GitHub configuration
│   ├── ISSUE_TEMPLATE/          # Issue templates
│   ├── PULL_REQUEST_TEMPLATE.md # PR template
│   ├── dependabot.yml           # Dependency automation
│   ├── labeler.yml              # Auto-labeling rules
│   └── workflows/               # CI/CD workflows
├── pyproject.toml               # Project metadata & dependencies
├── Makefile                     # Development commands
├── Containerfile                # UBI Python 3.12 container build
├── compose.yaml                 # Podman/Docker Compose orchestration
├── CONTRIBUTING.md              # Contribution guide
├── SECURITY.md                  # Security policy
├── CHANGELOG.md                 # Release history
├── LICENSE                      # Apache 2.0
└── README.md                    # Project documentation
```

## Key Components

- **`main.py`**: Application entry point with configuration validation, error handling, and uvicorn server startup
- **`api.py`**: FastAPI application setup with transport protocol selection (HTTP/SSE/streamable-HTTP) and health endpoints
- **`mcp.py`**: Core MCP server class that registers tools using FastMCP decorators
- **`settings.py`**: Environment-based configuration using Pydantic BaseSettings with validation
- **`tools/keycloak_tools.py`**: Read-only Keycloak tool schemas, pagination, and selected response fields
- **`keycloak_api/service.py`**: Request-scoped user tokens and asynchronous Admin API GET requests
- **`keycloak_api/auth.py`**: RFC 9728 resource server: verifies each request's Keycloak access token (JWKS, issuer, audience, `azp`) and accepts gateway-exchanged tokens; stores nothing (see [Authentication Guide](authentication.md))
- **`utils/pylogger.py`**: Structured JSON logging using structlog with comprehensive processors

## Current MCP Tools

There are twenty read-only tools covering realm settings, users, groups, clients,
roles, client scopes and mappers, events, sessions, token previews and LDAP federation.
See [Keycloak tools](keycloak-tools.md) for configuration, response fields, and pagination.

## HTTP Endpoints

The FastAPI application (`api.py`) exposes the following HTTP routes. The protected resource metadata is registered only when `ENABLE_AUTH=True`.

### Core Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — returns `{"status": "healthy"}` |
| `POST` | `/mcp` | MCP JSON-RPC endpoint (tools/list, tools/call, etc.) |
| `GET` | `/.well-known/oauth-protected-resource/mcp` | RFC 9728 protected resource metadata naming the Keycloak realm (auth only) |

There are no authorization, token, callback or registration routes: MCP clients
sign users in at Keycloak itself. The MCP endpoint requires a Keycloak access token
for both discovery and calls when `ENABLE_AUTH=True`. `/health` and the protected
resource metadata remain public.

## Error Handling

The server uses a layered error handling strategy:

| Layer | Mechanism | Example |
|-------|-----------|---------|
| **Startup** | `main.py` validates settings and catches `SystemExit`, `KeyboardInterrupt`, and unexpected exceptions before the event loop starts | Missing required env var → logged + exit(1) |
| **Middleware** | FastMCP verifies each Keycloak access token against the realm's JWKS, rejecting invalid or missing credentials with `401` and the metadata URL | Expired bearer token → `401` |
| **Health** | `/health` returns `200` with `{"status": "healthy"}` — no auth required | Used by container probes and load balancers |
| **Tool-level** | Each MCP tool validates its own inputs (type checks, value ranges) and raises descriptive errors that the MCP protocol returns to the client | `search_users` with `max_results=101` → error message in MCP response |
| **Logging** | All layers log via structlog with JSON output, including request IDs and error context | Structured `error` level entries with stack traces |

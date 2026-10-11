# Development Guide

## Prerequisites

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (fast Python package installer and resolver)
- make (macOS: `xcode-select --install`, Linux: `sudo apt install make` or `sudo dnf install make`)
- [Podman](https://podman.io/docs/installation) and [podman-compose](https://github.com/containers/podman-compose#installation) (required for containerized deployment)

## Setup

1. **Install uv (if not already installed):**

   ```bash
   # On macOS/Linux:
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # On MacOS using brew
   brew install uv

   # On Windows:
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

   ```
2. **Clone the repository:**

   ```bash
   git clone https://github.com/drtinkerer/keycloak-mcp-server
   cd keycloak-mcp-server
   ```
3. **Sync the locked project dependencies and install hooks:**

   ```bash
   uv sync --locked
   uv run pre-commit install
   ```

   uv manages the Python environment automatically. No activation is needed.
   Development dependencies are in the default `dev` group in `pyproject.toml`;
   `uv.lock` records exact versions. Add dependencies with `uv add` (or
   `uv add --dev` for development tools), then commit both files.

4. **Configure the server:**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration.
   ```

5. **Run the server:**

   ```bash
   uv run keycloak-mcp-server
   ```

   `make install` and `make local` wrap these uv commands.

## Configuration Options

The server configuration is managed through environment variables:

| Variable                      | Default                   | Description                                                               |
| ----------------------------- | ------------------------- | ------------------------------------------------------------------------- |
| `MCP_HOST`                  | `localhost`             | Server bind address                                                       |
| `MCP_PORT`                  | `5001`                  | Server port (1024-65535)                                                  |
| `MCP_TRANSPORT_PROTOCOL`    | `http`                  | Transport protocol (`http`, `sse`, `streamable-http`)               |
| `MCP_HOST_ENDPOINT`         | `http://localhost:5001` | Public-facing host URL (used in OAuth discovery responses)                |
| `MCP_SSL_KEYFILE`           | `None`                  | SSL private key file path                                                 |
| `MCP_SSL_CERTFILE`          | `None`                  | SSL certificate file path                                                 |
| `PYTHON_LOG_LEVEL`          | `INFO`                  | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `ENVIRONMENT`               | `development`           | Deployment environment identifier                                         |
| `ENABLE_AUTH`               | `True`*                 | Enable OAuth authentication (see[Auth Guide](authentication.md))             |
| `COMPATIBLE_WITH_CURSOR`    | `False`                 | Cursor IDE OAuth2 compatibility mode                                      |
| `CORS_ENABLED`              | `False`                 | Enable CORS middleware                                                    |
| `CORS_ORIGINS`              | `["*"]`                 | Allowed CORS origins                                                      |

*\* `ENABLE_AUTH` defaults to `True` in code but `False` in `.env.example`. Always copy `.env.example` to `.env`.*

See the [Authentication Guide](authentication.md) for the `KEYCLOAK_*` OAuth settings.

## Authentication

By default, `.env.example` ships with `ENABLE_AUTH=False` so you can start developing without an SSO provider. When you are ready to test the OAuth flow, see the [Authentication Guide](authentication.md) for:

- How browser OAuth supplies each user’s identity
- How to configure the public Keycloak client with PKCE
- Environment variable reference
- Troubleshooting 401 errors

## Verify Installation

1. **Health check:**

   ```bash
   curl http://localhost:5001/health
   ```
2. **Test MCP tools:**

   ```bash
   # Discover tools without Keycloak credentials
   uv run python examples/keycloak_client.py --list-only

   # After configuring Keycloak (see keycloak-tools.md), call the tools
   uv run python examples/keycloak_client.py
   ```

## Running Tests

### Development Environment Setup

If you followed the [Setup](#setup) steps above, dev dependencies and pre-commit hooks are already installed. Otherwise:

```bash
uv sync --locked
uv run pre-commit install
```

### Test Commands

The project includes a comprehensive test suite covering unit tests, integration tests, and various edge cases. To see the current count, run `uv run pytest --collect-only -q`.

1. **Run all tests:**

   ```bash
   uv run pytest
   ```
2. **Run tests with coverage reporting:**

   ```bash
   uv run pytest --cov=keycloak_mcp_server --cov-report=html --cov-report=term
   ```
3. **Run tests by category:**

   ```bash
   # Unit tests only
   uv run pytest -m unit

   # Integration tests only
   uv run pytest -m integration

   # Slow running tests
   uv run pytest -m slow

   # Tests requiring network access
   uv run pytest -m network
   ```
4. **Run specific test modules:**

   ```bash
   # Test individual components
   uv run pytest tests/test_keycloak.py -v
   uv run pytest tests/test_settings.py -v
   uv run pytest tests/test_mcp.py -v
   uv run pytest tests/test_api.py -v
   uv run pytest tests/test_oauth_service.py -v

   # Run basic integration tests
   uv run pytest tests/test_basic.py -v
   ```
5. **Run tests with different output formats:**

   ```bash
   # Verbose output with detailed test names
   uv run pytest -v

   # Short traceback format
   uv run pytest --tb=short

   # Quiet output (minimal)
   uv run pytest -q
   ```

### Test Suite Overview

| Test Category               | Count    | Description                               |
| --------------------------- | -------- | ----------------------------------------- |
| **Unit Tests**        | Majority | Individual component testing with mocking |
| **Integration Tests** | ~10%     | End-to-end workflow testing               |

**Test Files:**

- `test_keycloak.py` - Keycloak tool discovery, SDK requests, permissions, pagination, and token isolation
- `test_settings.py` - Configuration and environment variable tests
- `test_mcp.py` - MCP server initialization and tool registration tests
- `test_api.py` - FastAPI endpoint and health check tests
- `test_main.py` - Application entry point tests
- `test_oauth_*.py` - OAuth controller, handler, and service tests
- `test_storage_*.py` - Storage initialization and service tests
- `test_basic.py` - Integration and package structure tests
- `test_utils.py` - Utility and logging tests

**Test Features:**

- Comprehensive error handling validation
- Async function testing support
- Mock external dependencies
- Environment isolation with fixtures
- Performance testing for large data
- Concurrent usage simulation
- Configuration validation testing

## Code Quality Checks

1. **Linting and formatting with Ruff:**

   ```bash
   # Check for issues
   uv run ruff check .

   # Auto-fix issues
   uv run ruff check . --fix

   # Format code
   uv run ruff format .
   ```
2. **Type checking with MyPy:**

   ```bash
   uv run mypy keycloak_mcp_server/
   ```
3. **Docstring validation:**

   ```bash
   pydocstyle keycloak_mcp_server/ --convention=google
   ```
4. **Run all pre-commit checks:**

   ```bash
   uv run pre-commit run --all-files
   ```

## Makefile Targets

Run `make help` to see all available commands:

```bash
make help       # Show all targets
make install    # Install dependencies
make test       # Run test suite
make lint       # Run linters
make format     # Format code
make coverage   # Generate coverage report
make pre-commit # Run pre-commit hooks
make local      # Run server locally
```

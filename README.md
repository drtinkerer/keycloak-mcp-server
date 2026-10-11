# Keycloak MCP Server

[![Python 3.12+](https://img.shields.io/badge/python-3.12,3.13-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/drtinkerer/keycloak-mcp-server/actions/workflows/test.yml/badge.svg)](https://github.com/drtinkerer/keycloak-mcp-server/actions/workflows/test.yml)
[![Coverage](https://codecov.io/gh/drtinkerer/keycloak-mcp-server/branch/main/graph/badge.svg)](https://codecov.io/gh/drtinkerer/keycloak-mcp-server)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/drtinkerer/keycloak-mcp-server)

An MCP server for the Keycloak Admin REST API where **every call runs as the
signed-in user**. Users log in to Keycloak from their MCP client (Claude Code,
Codex, …); the server forwards *their own* access token to the Admin API, so
Keycloak enforces each user's existing roles. There is no shared admin
password or service account.

```
MCP client ──login (OAuth 2.1 + PKCE)──▶ this server ──▶ Keycloak login page
     │                                        │ keeps the user's Keycloak token, encrypted
     └── tool call with an MCP-only token ──▶ │ ──Admin API, Bearer <user's token>──▶ Keycloak
                                                                    (decides: allowed / 403)
```

## Why per-user identity

Most Keycloak MCP servers authenticate with one admin account or service account,
so anyone who can reach the server acts with its full power, and the audit trail
shows the shared account instead of the person. Here:

- **Least privilege for free**: a user with only `view-clients` can list clients
  and gets a clean permission error for everything else.
- **Audit trails name the real user**.
- **No admin password or client secret to store**: the Keycloak client is public, with PKCE.
- **The MCP client never holds the Keycloak token**: the
  [FastMCP OAuth proxy](https://gofastmcp.com/servers/auth/oauth-proxy) issues it a
  token that only works on this server, and stores the Keycloak token encrypted.
- **No fallback identity**: requests are never retried as a service account, and the
  SDK is prevented from silently logging in again.

## Features

- **20 read-only tools**: realm settings; users, groups and memberships; clients,
  client scopes and protocol mappers; direct and effective role mappings and role
  holders; login and admin events; sessions; access-token previews; LDAP federation
- **Browser OAuth** for any MCP client, with consent, PKCE, audience-bound JWT
  validation and encrypted per-user token storage, with no database
- **Bounded, safe output**: pagination capped at 100, selected fields only; secrets,
  credentials and admin-event payloads are never returned
- **FastMCP + FastAPI**, HTTP/SSE/streamable-HTTP transports, Pydantic settings,
  structured JSON logging
- **Container and OpenShift manifests**, GitHub Actions CI

### Current limits

- Read-only: no create, update or delete tools yet.
- One realm per server: the realm users sign in to is the realm the tools inspect.
- Single instance: OAuth state is stored on local disk.

## Quick Start

```bash
git clone https://github.com/drtinkerer/keycloak-mcp-server
cd keycloak-mcp-server
make install        # uv sync + pre-commit hooks + local configuration
make local          # starts server on localhost:5001
```

Verify in another terminal:

```bash
curl http://localhost:5001/health
```

<details>
<summary>Manual setup (without Make)</summary>

```bash
# Sync dependencies from the committed lockfile
uv sync --locked
uv run pre-commit install

# Configure and run
cp .env.example .env
uv run keycloak-mcp-server

# Verify
curl http://localhost:5001/health
```

</details>

All Python commands go through `uv`; no shell activation is needed. `uv sync`
installs the default `dev` dependency group. `uv.lock` is committed for reproducible
installs; use `uv add` to add dependencies and `uv lock --upgrade-package <name>`
to update one. uv manages an ignored environment internally.
See [uv’s project guide](https://docs.astral.sh/uv/guides/projects/).

## Configure browser OAuth

Register a public OIDC client named **`keycloak-mcp`** in the target Keycloak
realm. Enable authorization code flow with PKCE `S256`, refresh tokens, and an
access-token audience mapper that adds the client's own ID (`keycloak-mcp`).
Disable password grants and service accounts. Register this exact redirect URI:

```text
http://localhost:5001/auth/callback
```

The [authentication guide](docs/authentication.md) lists every client setting and
includes a Terraform example.

Set these values in the server’s ignored `.env` file:

```dotenv
ENABLE_AUTH=True
MCP_HOST_ENDPOINT=http://localhost:5001
KEYCLOAK_SERVER_URL=https://keycloak.example.com
KEYCLOAK_REALM=myrealm
KEYCLOAK_OAUTH_CLIENT_ID=keycloak-mcp
KEYCLOAK_OAUTH_AUDIENCE=keycloak-mcp
KEYCLOAK_OAUTH_SIGNING_KEY=<random persistent signing key>
# KEYCLOAK_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
```

The client uses public authorization code flow with PKCE; no Keycloak client
secret is required. Leave `KEYCLOAK_OAUTH_CLIENT_SECRET` unset. For an existing
confidential upstream client, supplying that variable selects `client_secret_basic`.
The signing key below is internal MCP server state, separate from Keycloak credentials.

Generate the signing key once with
`uv run python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
Keep it private and stable across restarts. Remove `KEYCLOAK_DEV_ACCESS_TOKEN` if set.
The server fails at startup if OAuth configuration is incomplete. No PostgreSQL
is required; encrypted OAuth state is stored in the ignored `.keycloak-oauth/` directory.

Start the server with `make local`. The MCP client opens the browser, and the
user signs in to Keycloak. Their existing Keycloak permissions determine which tools they
can call; the client registration grants no admin roles. Target URL, realm, and
OAuth client ID belong to the server configuration. Each user’s identity
comes from the browser login. See the [authentication guide](docs/authentication.md)
for the flow, TLS trust, and troubleshooting.

## Use with Codex

With the configured server running:

```bash
codex mcp add keycloak --url http://localhost:5001/mcp
codex mcp login keycloak
codex mcp list
codex mcp get keycloak
codex
```

Adding an OAuth server may start login automatically; run `login` if authentication
is still needed or if the connection was already added with authentication disabled.
The user approves the MCP connection and completes Keycloak login in the browser.
No client secret or Keycloak access token goes into the Codex MCP configuration.

Inside a new Codex session, use `/mcp` to check the twenty tools, then ask:

> Use the keycloak MCP server to find who has the realm-admin role.

The CLI saves the connection in `~/.codex/config.toml` by default. Restart existing
Codex sessions after changing MCP configuration.
See the [official Codex MCP documentation](https://developers.openai.com/codex/mcp).

Log out or remove the connection:

```bash
codex mcp logout keycloak
codex mcp remove keycloak
```

## Use with Claude Code

```bash
claude mcp add --transport http --scope user keycloak http://localhost:5001/mcp
claude mcp login keycloak
claude mcp list
claude mcp get keycloak
claude
```

In Claude, `/mcp` shows connection status and offers authentication. Use it if
an older Claude CLI does not have `mcp login`. The `user` scope makes the connection
available across projects. Start a new Claude session after changing the configuration.
See the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

Log out or remove the connection:

```bash
claude mcp logout keycloak
claude mcp remove --scope user keycloak
```

Removing a connection does not stop the server. Press `Ctrl+C` in its terminal to
stop it. With `ENABLE_AUTH=False`, discovery works without login, but browser OAuth
routes are absent. Use [the direct client](docs/keycloak-tools.md) to test the protocol
without an LLM.

## Configuration

| Variable                      | Default       | Description                                                          |
| ----------------------------- | ------------- | -------------------------------------------------------------------- |
| `MCP_HOST`                  | `localhost` | Server bind address                                                  |
| `MCP_PORT`                  | `5001`      | Server port (1024-65535)                                             |
| `MCP_TRANSPORT_PROTOCOL`    | `http`      | Transport protocol (`http`, `sse`, `streamable-http`)          |
| `MCP_SSL_KEYFILE`           | `None`      | SSL private key file path                                            |
| `MCP_SSL_CERTFILE`          | `None`      | SSL certificate file path                                            |
| `ENABLE_AUTH`               | `False`*    | Enable OAuth authentication (see [Auth Guide](docs/authentication.md)) |
| `MCP_HOST_ENDPOINT` | `http://localhost:5001` | Public OAuth origin, without `/mcp` |
| `KEYCLOAK_SERVER_URL` | Unset | Keycloak base URL, including `/auth` when used |
| `KEYCLOAK_REALM` | Unset | Target realm, e.g. `myrealm` |
| `KEYCLOAK_OAUTH_CLIENT_ID` | Unset | Registered public Keycloak client with PKCE |
| `KEYCLOAK_OAUTH_CLIENT_SECRET` | Unset | Optional; only set for a confidential upstream client |
| `KEYCLOAK_OAUTH_SIGNING_KEY` | Unset | Persistent random key of at least 32 characters |
| `KEYCLOAK_OAUTH_AUDIENCE` | `keycloak-mcp` | Required `aud` claim; add an audience mapper for it |
| `PYTHON_LOG_LEVEL`          | `INFO`      | Logging level                                                        |

*\* `ENABLE_AUTH` is `True` in code but `False` in `.env.example`, so a fresh `make install` starts with tool discovery only.*

## Documentation

| Guide                                 | Description                                                |
| ------------------------------------- | ---------------------------------------------------------- |
| [Architecture](docs/architecture.md)     | System diagrams, code structure, key components, MCP tools |
| [Development](docs/development.md)       | Setup, running locally, testing, code quality              |
| [Deployment](docs/deployment.md)         | Podman, OpenShift, container configuration                 |
| [CI/CD](docs/ci-cd.md)                   | Workflows, pipeline features, running CI locally           |
| [Contributing](CONTRIBUTING.md)          | Development workflow, commit conventions, PR process       |
| [Security](SECURITY.md)                  | Vulnerability reporting policy                             |
| [Changelog](CHANGELOG.md)                | Release history                                            |
| [Authentication](docs/authentication.md) | OAuth setup, auth modes, troubleshooting                   |
| [Tutorial](docs/tutorial.md)             | Your First Tool in 5 Minutes                               |
| [Examples](examples/)                    | FastMCP and LangGraph client examples                      |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

```bash
# Fork, clone, and set up
git clone https://github.com/<your-username>/keycloak-mcp-server.git
cd keycloak-mcp-server
make install

# Create a branch, make changes, verify
git checkout -b feat/your-feature
make lint && make test && make pre-commit

# Commit and open a PR
git commit -m "feat: your descriptive message"
git push origin feat/your-feature
```

## Acknowledgements

Started from [redhat-data-and-ai/template-mcp-server](https://github.com/redhat-data-and-ai/template-mcp-server)
(Apache 2.0), a FastMCP server template. The Keycloak integration, OAuth proxy setup and tools are new.

## License

[Apache 2.0](LICENSE)

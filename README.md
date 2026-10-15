# Keycloak MCP Server

[![Python 3.12+](https://img.shields.io/badge/python-3.12%20|%203.13%20|%203.14-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/drtinkerer/keycloak-mcp-server/actions/workflows/test.yml/badge.svg)](https://github.com/drtinkerer/keycloak-mcp-server/actions/workflows/test.yml)
[![Coverage](https://codecov.io/gh/drtinkerer/keycloak-mcp-server/branch/main/graph/badge.svg)](https://codecov.io/gh/drtinkerer/keycloak-mcp-server)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/drtinkerer/keycloak-mcp-server)
[![M8ven Score](https://m8ven.ai/badge/mcp/drtinkerer-keycloak-mcp-server-cbzxll?v=2fcfb8e724b3bb231a1f025ca32a655d)](https://m8ven.ai/mcp/drtinkerer-keycloak-mcp-server-cbzxll?s=readme)

An MCP server for the Keycloak Admin REST API where **every call runs as the
signed-in user**. Users log in to Keycloak from their MCP client (Claude Code,
…) in the browser; the server checks *their own* access token and sends it to the
Admin API, so Keycloak enforces each user's existing roles. There is no shared
admin password or service account, and the server stores nothing.

```
MCP client ──browser login (OAuth 2.1 + PKCE)──▶ Keycloak
     │
     └── tool call, Bearer <user's token> ──▶ this server ──Admin API, same token──▶ Keycloak
                     (verifies signature, audience, client)          (decides: allowed / 403)
```

## Why per-user identity

Most Keycloak MCP servers authenticate with one admin account or service account,
so anyone who can reach the server acts with its full power, and the audit trail
shows the shared account instead of the person. Here:

- **Least privilege for free**: a user with only `view-clients` can list clients
  and gets a clean permission error for everything else.
- **Audit trails name the real user**.
- **No admin password, client secret or signing key to store**: the Keycloak client is
  public, with PKCE.
- **Stateless**: tokens are verified against the realm's keys on every request; there
  is no session store or volume, so it scales to any number of replicas.
- **No fallback identity**: requests are never retried as a service account, and the
  SDK is prevented from silently logging in again.

## Features

- **20 read-only tools**: realm settings; users, groups and memberships; clients,
  client scopes and protocol mappers; direct and effective role mappings and role
  holders; login and admin events; sessions; access-token previews; LDAP federation
- **Browser sign-in** through Keycloak itself (RFC 9728 discovery, PKCE), with
  audience- and client-bound JWT validation and no database or storage
- **Gateway support**: tokens a gateway such as ContextForge obtained by RFC 8693
  token exchange are accepted from configured clients
- **Bounded, safe output**: pagination capped at 100, selected fields only; secrets,
  credentials and admin-event payloads are never returned
- **FastMCP + FastAPI**, HTTP/SSE/streamable-HTTP transports, Pydantic settings,
  structured JSON logging
- **Container and OpenShift manifests**, GitHub Actions CI

### Current limits

- Read-only: no create, update or delete tools yet.
- One realm per server: the realm users sign in to is the realm the tools inspect.
- stdio mode does not check the token's audience locally; Keycloak validates it on every Admin API call.
- The MCP client holds the user's Keycloak token, which also works against the Admin
  API directly until it expires.
- MCP clients need a pre-registered client ID (Claude Code: `--client-id`); Keycloak
  dynamic client registration is not used.

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

## Configure browser sign-in

Register a public OIDC client named **`keycloak-mcp`** in the target Keycloak
realm. Enable authorization code flow with PKCE `S256`, refresh tokens, and an
access-token audience mapper that adds the client's own ID (`keycloak-mcp`).
Disable password grants and service accounts. MCP clients sign in with this client
directly, so register their loopback callback, e.g. for Claude Code with
`--callback-port 9876`:

```text
http://localhost:9876/*
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
# KEYCLOAK_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
```

No client secret, signing key, database or volume is needed. Remove
`KEYCLOAK_DEV_ACCESS_TOKEN` if set. The server fails at startup if the configuration
is incomplete or insecure.

Start the server with `make local`. The MCP client finds Keycloak through the server's
protected resource metadata, opens the browser, and the user signs in to Keycloak.
Their existing Keycloak permissions determine which tools they can call; the client
registration grants no admin roles. See the [authentication guide](docs/authentication.md)
for the flow, TLS trust, gateways, and troubleshooting.

## Use with Codex

HTTP mode needs an MCP client that accepts a pre-registered OAuth client ID. Codex
registers OAuth clients dynamically, which this server does not support, so run it
in stdio mode, where the server signs you in itself (see the next section for the
redirect URI to register):

```bash
codex mcp add keycloak \
  --env KEYCLOAK_SERVER_URL=https://keycloak.example.com \
  --env KEYCLOAK_REALM=myrealm \
  --env KEYCLOAK_OAUTH_CLIENT_ID=keycloak-mcp \
  -- uvx keycloak-mcp-server --stdio
codex mcp list
```

Inside a new Codex session, use `/mcp` to check the twenty tools, then ask:

> Use the keycloak MCP server to find who has the realm-admin role.

See the [official Codex MCP documentation](https://developers.openai.com/codex/mcp).

## Use with Claude Code: stdio, no server to run

Claude Code starts the server with `uvx` and talks to it over stdin/stdout. On the
first tool call the server opens the Keycloak sign-in in your browser itself
(authorization code + PKCE, loopback callback), then uses **your** token for every
Admin API call. Tokens are cached in `~/.cache/keycloak-mcp-server/tokens.json`
(mode `0600`) and refreshed automatically.

Add `http://127.0.0.1:8250/callback` as a valid redirect URI on the `keycloak-mcp`
client, then:

```bash
claude mcp add --scope user keycloak \
  -e KEYCLOAK_SERVER_URL=https://keycloak.example.com \
  -e KEYCLOAK_REALM=myrealm \
  -e KEYCLOAK_OAUTH_CLIENT_ID=keycloak-mcp \
  -- uvx keycloak-mcp-server --stdio
```

No server settings beyond these are needed in this mode. It is for one person
on their own machine; to share one server between users, use HTTP mode below. Set
`KEYCLOAK_LOGIN_PORT` (and register that port's `/callback`) if 8250 is taken. To sign
out, delete the cache file.

## Use with Claude Code: HTTP

```bash
claude mcp add --transport http --scope user \
  --client-id keycloak-mcp --callback-port 9876 \
  keycloak http://localhost:5001/mcp
claude mcp list
claude
```

In Claude, `/mcp` shows connection status and starts the browser sign-in. The
callback port must match a redirect URI on the `keycloak-mcp` client. The `user`
scope makes the connection available across projects. Start a new Claude session
after changing the configuration.
See the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

Log out or remove the connection:

```bash
claude mcp logout keycloak
claude mcp remove --scope user keycloak
```

Removing a connection does not stop the server. Press `Ctrl+C` in its terminal to
stop it. With `ENABLE_AUTH=False`, discovery works without login, but there is no
browser sign-in. Use [the direct client](docs/keycloak-tools.md) to test the protocol
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
| `MCP_HOST_ENDPOINT` | `http://localhost:5001` | Public origin MCP clients reach, without `/mcp` |
| `KEYCLOAK_SERVER_URL` | Unset | Keycloak base URL, including `/auth` when used |
| `KEYCLOAK_REALM` | Unset | Target realm, e.g. `myrealm` |
| `KEYCLOAK_OAUTH_CLIENT_ID` | Unset | Public Keycloak client users sign in with (required `azp`) |
| `KEYCLOAK_OAUTH_AUDIENCE` | `keycloak-mcp` | Required `aud` claim; add an audience mapper for it |
| `KEYCLOAK_TRUSTED_EXCHANGERS` | `[]` | HTTP mode: gateway clients whose RFC 8693 exchanged tokens are accepted, e.g. `["mcp-context-forge"]` |
| `KEYCLOAK_CA_BUNDLE` | Unset | PEM bundle for a private Keycloak certificate issuer |
| `KEYCLOAK_LOGIN_PORT` | `8250` | stdio mode: loopback port for the sign-in callback |
| `KEYCLOAK_TOKEN_CACHE_DIR` | `~/.cache/keycloak-mcp-server` | stdio mode: token cache directory |
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

## Credits

This project is built on
[redhat-data-and-ai/template-mcp-server](https://github.com/redhat-data-and-ai/template-mcp-server),
a production-ready FastMCP server template from the `redhat-data-and-ai` GitHub
organization, licensed under Apache 2.0. Thanks to its maintainers and contributors: the project structure,
FastAPI/FastMCP application setup, Pydantic settings, structured logging, container
build, OpenShift manifests, Makefile, CI workflows, pre-commit configuration and
contributor docs all come from their work.

### Changes from the template

- **Removed** the example tools (multiply, code review prompt, logo), the template's
  own OAuth server with PostgreSQL token storage, and their settings and tests.
- **Added** Keycloak per-user authentication: stateless Keycloak token verification
  (RFC 9728 resource server, optional RFC 8693 gateway tokens) for HTTP mode, and a
  built-in browser sign-in (PKCE, loopback callback, token cache) for stdio mode.
- **Added** twenty read-only Keycloak Admin API tools, run with the signed-in user's token.
- **Updated** to FastMCP 4, uv with a committed lockfile, dependency ranges for
  `uvx`/`pip` installs, and Python 3.12–3.14.
- **Updated** the container, Compose and OpenShift manifests for Keycloak settings
  and CI for Node 24 actions and PyPI trusted publishing.
- **Renamed** the package to `keycloak_mcp_server` and rewrote the docs for Keycloak.

## License

[Apache 2.0](LICENSE)

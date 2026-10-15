# Changelog

All notable changes to this project will be documented in this file.

This project uses [GitHub Releases](https://github.com/drtinkerer/keycloak-mcp-server/releases) for detailed changelogs. Release notes are auto-generated from merged pull request titles and labels.

## [0.1.3] - 2026-10-15

### Changed

- Tools are registered one by one instead of in a loop, so registries that read
  tools from the source (static analysis) list all twenty. No behaviour change.

## [0.1.2] - 2026-10-13

### Added

- Gateway token exchange (RFC 8693) in HTTP mode: with `KEYCLOAK_TRUSTED_EXCHANGERS`
  set (e.g. `["mcp-context-forge"]`), Keycloak access tokens that a trusted gateway
  exchanged for this server's audience are accepted next to the OAuth proxy's own
  tokens, and forwarded to the Admin API unchanged, so Keycloak still authorizes the
  original user. Off by default. A plain sign-in token for this server's own client
  cannot use this path.
- Multi-arch container image (`linux/amd64`, `linux/arm64`) on
  `ghcr.io/drtinkerer/keycloak-mcp-server`, built, smoke-tested and pushed by CI.

### Changed

- The image listens on `0.0.0.0` by default.
- README credits the upstream template and summarizes the changes from it.

## [0.1.1] - 2026-10-11

### Added

- stdio mode (`keycloak-mcp-server --stdio`): MCP clients launch the server with `uvx`
  and it signs the local user in through the browser (PKCE, loopback callback), caches
  tokens in a `0600` file and refreshes them. No OAuth proxy settings are needed.

### Fixed

- `.env` is read from the working directory. It was searched for from the package's
  install directory, so `uvx` and `pip` installs never read it.
- Logs go to stderr, keeping stdout for the stdio protocol.
- Declare dependency ranges instead of exact pins, so `uvx` and `pip` can resolve
  versions with wheels for the user's Python. 0.1.0 failed to install on Python 3.14
  (no `pydantic-core` wheel for the pinned version).

### Changed

- FastMCP 4.1. Its token verifier uses `httpx2`, so the JWKS client in the OAuth
  proxy now does too; `httpx2` is a declared dependency.
- Tested on Python 3.12, 3.13 and 3.14.
- CI actions moved to Node.js 24 releases; runner pinned to `ubuntu-24.04`.
- Dependabot uses the `uv` ecosystem, so `uv.lock` is updated with `pyproject.toml`.

## [0.1.0] - 2026-10-11

### Added

- Browser OAuth through the FastMCP OAuth proxy: each user signs in to Keycloak and
  every Admin API call uses that user's own access token.
- Upstream token verification: signature, issuer, audience, authorized party,
  token type, subject, expiry and not-before.
- Encrypted per-user OAuth state on local disk; no database.
- Twenty read-only tools: realm settings, users, groups and memberships, clients,
  client scopes and protocol mappers, role mappings and role holders, login and
  admin events, sessions, access-token previews and LDAP federation.
- Localhost-only development token mode for single-user debugging.
- Container image, Compose file and OpenShift manifests with persistent OAuth storage.
- Test suite with simulated Keycloak Admin API and OAuth endpoints.

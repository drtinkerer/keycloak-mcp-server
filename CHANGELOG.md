# Changelog

All notable changes to this project will be documented in this file.

This project uses [GitHub Releases](https://github.com/drtinkerer/keycloak-mcp-server/releases) for detailed changelogs. Release notes are auto-generated from merged pull request titles and labels.

## [0.1.1] - 2026-10-11

### Fixed

- Declare dependency ranges instead of exact pins, so `uvx` and `pip` can resolve
  versions with wheels for the user's Python. 0.1.0 failed to install on Python 3.14
  (no `pydantic-core` wheel for the pinned version).

### Changed

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

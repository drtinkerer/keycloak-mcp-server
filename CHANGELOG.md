# Changelog

All notable changes to this project will be documented in this file.

This project uses [GitHub Releases](https://github.com/drtinkerer/keycloak-mcp-server/releases) for detailed changelogs. Release notes are auto-generated from merged pull request titles and labels.

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

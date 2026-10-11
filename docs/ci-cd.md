# Continuous Integration & Deployment

This project uses GitHub Actions for automated CI/CD workflows to ensure code quality, security, and reliability.

## CI/CD Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| **Tests** | Push to main, PRs | Run test suite with coverage (80% minimum) |
| **Pre-commit** | Push to main, PRs | Linting, formatting, type checking, security scans |
| **Labeler** | PR opened/updated | Auto-label PRs by changed file paths |
| **Publish to PyPI** | Release published, or manual run | Build, `twine check`, test on 3.12/3.13, then publish via trusted publishing (manual runs can target Test PyPI) |
| **Stale** | Weekly (Monday) | Mark and close inactive issues/PRs |
| **Dependabot** | Weekly (Monday) | Automated dependency update PRs |

## CI Pipeline Features

### Code Quality Assurance
- Multi-Python version testing (3.12, 3.13)
- Comprehensive test suite execution
- Code coverage reporting (80%+ requirement)
- Ruff linting and formatting validation
- MyPy type checking
- Docstring validation with pydocstyle

### Security & Compliance
- Bandit security linting
- Safety dependency vulnerability scanning

### Automation & Maintenance
- Dependabot configuration for automated dependency updates
- Pre-commit hook automation
- Weekly security audits
- Automated PR creation for dependency updates

### Concurrency Controls
All workflows include concurrency blocks to prevent redundant runs:
- PR workflows cancel in-progress runs when new commits are pushed
- Publish runs always run to completion
- Stale bot is limited to one concurrent run

## Running CI Checks Locally

CI installs dependencies with `uv sync --locked` and runs tools through `uv run`.
The test job sets `ENABLE_AUTH=False`; OAuth integration tests supply their own
simulated configuration. Before pushing code, run the same checks locally:

```bash
# Install development dependencies
uv sync --locked

# Run all pre-commit checks
uv run pre-commit run --all-files

# Run tests with coverage
uv run pytest --cov=keycloak_mcp_server --cov-fail-under=80

# Run security checks
uv run bandit -r keycloak_mcp_server/
uv run safety check

# Build and test container
podman build -t keycloak-mcp-server .
podman run --rm keycloak-mcp-server uv run --no-sync python -c "import keycloak_mcp_server; print('OK')"
```

## Branch Protection

The `main` branch is protected with the following requirements:
- All CI checks must pass
- Pull request reviews required
- Up-to-date branches required
- No direct pushes to main

## Releasing to PyPI

Publishing uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/),
so no API token is stored in GitHub.

1. Bump `version` in `pyproject.toml` (`uv version --bump patch|minor|major`), update
   `CHANGELOG.md`, and merge to `main`.
2. Create the release; its tag must be `v` plus the package version:

   ```bash
   gh release create v0.1.0 --generate-notes
   ```

3. `publish-pypi.yml` builds, tests, and publishes. To try a build first, run the
   workflow manually with **Publish to Test PyPI** checked.

Create releases as yourself (the GitHub UI or `gh`). A release created by a workflow
using `GITHUB_TOKEN` does not trigger other workflows, so it would not publish.

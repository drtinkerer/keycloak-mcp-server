FROM ghcr.io/astral-sh/uv:0.12.22 AS uv
FROM registry.access.redhat.com/ubi9/python-312:latest

USER root
WORKDIR /app
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_CACHE_DIR=/tmp/uv-cache

# Cache locked runtime dependencies separately from application source.
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --locked --no-dev --no-install-project

COPY keycloak_mcp_server /app/keycloak_mcp_server
RUN uv sync --locked --no-dev --no-editable \
    && mkdir -p /app/.keycloak-oauth \
    && chown -R 1001:0 /app \
    && chmod -R g=u /app
USER default
# Let uv select the project environment without inherited shell activation.
ENV VIRTUAL_ENV=""

EXPOSE 5001
# Runtime uses installed packages and needs no build-time cache access.
CMD ["uv", "run", "--no-sync", "--no-cache", "keycloak-mcp-server"]

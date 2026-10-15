# Deployment Guide

## Using Podman / Docker Compose

1. **Build and run with Podman Compose:**
   ```bash
   podman compose up --build
   ```

2. **Or build manually:**
   ```bash
   podman build -t keycloak-mcp-server .
   podman run -p 5001:5001 --env-file .env keycloak-mcp-server
   ```

## Deploying to OpenShift

The OpenShift manifests live in `deployment/openshift/` (Deployment, Service, Route, ConfigMap, Secret, BuildConfig, ImageStream, Kustomization).

Quick start:

```bash
# Deploy to OpenShift namespace
make deploy openshift NAMESPACE=your-project-name

# Remove deployment
make undeploy openshift
```

### Keycloak configuration

The server is stateless: it needs no database, volume, or secret. Configure the
`KEYCLOAK_*` values from the [authentication guide](authentication.md) and set the
public HTTPS origin in `MCP_HOST_ENDPOINT`. The OpenShift manifests run two replicas
with rolling updates; scale them freely. Edit `deployment/openshift/configmap.yaml`
for your Keycloak first.

## Container image

Multi-arch images (`linux/amd64`, `linux/arm64`) are published to GHCR by
`.github/workflows/container.yml`:

| Trigger | Tags |
| --- | --- |
| Push to `main` | `edge`, `sha-<commit>` |
| Release `vX.Y.Z` | `X.Y.Z`, `X.Y`, `latest` |
| Pull request | Built and smoke-tested, not pushed |

```bash
docker pull ghcr.io/drtinkerer/keycloak-mcp-server:latest
```

The image listens on `0.0.0.0:5001` and runs HTTP mode. Configure it with the
`KEYCLOAK_*` variables from the [authentication guide](authentication.md); it needs
no persistent storage.

## Container Build (`Containerfile`)

The container uses the Red Hat UBI Python 3.12 image and uv:

| Step | Behavior |
| --- | --- |
| uv installation | Copy pinned uv binaries from the official uv image |
| Dependencies | `uv sync --locked --no-dev --no-install-project` using `pyproject.toml` and `uv.lock` |
| Application | Copy source and run `uv sync --locked --no-dev --no-editable` |
| Permissions | Run as UBI’s non-root user with group-writable application storage |
| Entrypoint | `uv run --no-sync --no-cache keycloak-mcp-server` uses dependencies installed at build time |

Port **5001** is exposed. The container runtime does not resolve or download
packages at startup. When renaming the package, update the source `COPY` and
console-script command in `Containerfile`.

## Container Configuration

The `compose.yaml` maps container port 5001 to host port 5001 and overrides `MCP_HOST=0.0.0.0` so the server binds to all interfaces inside the container (required for port forwarding to work).

- **Health Check**: `curl -f http://localhost:5001/health`

## Manual Testing

1. **Container testing:**
   ```bash
   podman compose up -d
   curl -f http://localhost:5001/health
   podman compose down
   ```

2. **SSL testing (if configured):**
   ```bash
   curl -k https://localhost:5001/health
   ```

3. **Import validation:**
   ```bash
   podman run --rm keycloak-mcp-server uv run --no-sync python -c "import keycloak_mcp_server; print('OK')"
   ```

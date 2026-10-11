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

### OAuth configuration and storage

The Keycloak OAuth proxy does not use PostgreSQL. Configure the `KEYCLOAK_*` values
from the [authentication guide](authentication.md), set the public HTTPS origin
in `MCP_HOST_ENDPOINT`, and register the matching `/auth/callback` URI in Keycloak.
Mount persistent storage for the OAuth state directory and keep the signing key
stable. `compose.yaml` mounts a named volume at `/app/.keycloak-oauth`; the OpenShift
manifests mount a `ReadWriteOnce` PVC at `/data` and run a single replica. Edit
`deployment/openshift/configmap.yaml` and `secret.yaml` for your Keycloak first.

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

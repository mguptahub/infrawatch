# Release Notes

## v0.2.0

### Security — Non-root Containers

All containers now run as non-root users, reducing the attack surface.

- **Backend**: Runs as `appuser` with ownership of `/app` for celery beat schedule writes
- **Frontend (production)**: Runs as `nginx` user, listens on port 8080 instead of privileged port 80
- **Frontend (dev)**: Runs as built-in `node` user via docker-compose override
- **Celery worker/beat**: Inherit non-root from backend Dockerfile

### Helm Chart — Breaking Changes

Chart version bumped to `0.2.0` with structural changes to `values.yaml`.

#### Values restructured

- `backend`, `frontend`, `celeryWorker`, `celeryBeat`, `postgresql`, `valkey` moved under `services:`
- `externalPostgresql`, `externalValkey` moved under `externalServices:`

**Migration:** Update any custom values files to use the new paths:

```yaml
# Before
backend:
  replicas: 2
externalPostgresql:
  host: my-pg.example.com

# After
services:
  backend:
    replicas: 2
externalServices:
  postgresql:
    host: my-pg.example.com
```

#### Ports hardcoded

- Removed `service.frontendPort` and `service.backendPort` from values — ports are now fixed (frontend: 8080, backend: 8000)

#### Default values added

- `ingress.className`: `nginx`
- `app.adminEmail`: `admin@example.com`
- `smtp.from`: `noreply@example.com`
- `serviceAccount.name`: `infrawatch`

#### cert-manager integration

New `ingress.certManager` section supports automatic TLS certificate provisioning:

- **ClusterIssuer**: Reference an existing cluster-wide issuer
- **Namespace Issuer (HTTP-01)**: Chart creates the Issuer with HTTP solver
- **Namespace Issuer (DNS-01 Cloudflare)**: Chart creates the Issuer, Cloudflare API token Secret, and DNS solver config

### Docker Compose

- Production frontend port mapping changed from `3000:80` to `3000:8080`
- Dev override sets `user: node` for frontend container

---

## v0.1.0

Initial release.

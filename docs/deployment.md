# Deploying ArchFlow

ArchFlow is a single FastAPI process serving the REST API and the web UI,
with SQLite storage and a local artifacts directory — one container, one
volume. The UI's fonts are self-hosted, so no internet access is required
at runtime (only the optional draw.io embed and the Anthropic API reach out).

> **No built-in authentication.** ArchFlow trusts everyone who can reach it.
> Deploy it on a private network, behind your VPN, or behind an
> authenticating reverse proxy (OAuth2 proxy, Cloudflare Access, Tailscale)
> — never directly on the public internet.

## Docker (any host)

```bash
docker build -t archflow .
docker run -d --name archflow -p 8000:8000 \
  -v archflow-data:/data \
  -e ARCHFLOW_ANTHROPIC_API_KEY=sk-ant-...   # optional: enables the AI features
  archflow
```

The UI is at `http://<host>:8000/ui/`, interactive API docs at `/docs`.
State (database + generated artifacts) lives in the `archflow-data` volume.
All settings are `ARCHFLOW_*` environment variables — see `.env.example`
for the full list (Horizzon credentials, draw.io embed URL, model choice).

## Managed container platforms

The same image runs unchanged on Fly.io, Render, Railway, Azure Container
Apps, or Cloud Run. The two things every platform needs:

1. A **persistent volume** mounted at `/data` (SQLite lives there — without
   it every deploy starts empty).
2. The `ARCHFLOW_*` env vars as platform secrets — never bake keys into the
   image.

Example (Fly.io):

```bash
fly launch --no-deploy          # generates fly.toml from the Dockerfile
fly volumes create archflow_data --size 1
# add [mounts] source="archflow_data" destination="/data" to fly.toml
fly secrets set ARCHFLOW_ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

## Bare Python (a VM you already have)

```bash
python3 -m venv .venv && .venv/bin/pip install .
ARCHFLOW_DATABASE_URL=sqlite:////var/lib/archflow/archflow.db \
  .venv/bin/uvicorn archflow.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Put nginx/caddy (with TLS and auth) in front.

## Notes for production

- SQLite is right for a team-sized deployment (one process, one volume).
  Set `ARCHFLOW_DATABASE_URL` to Postgres if you outgrow it — the storage
  layer is plain SQLAlchemy.
- The draw.io editor loads from `ARCHFLOW_DRAWIO_EMBED_URL`
  (default: the public `embed.diagrams.net`). Point it at a self-hosted
  draw.io to keep diagram traffic inside your network.
- Horizzon publication needs the `ARCHFLOW_HORIZZON_*` variables; without
  them ArchFlow falls back to ArchiMate Open Exchange file export.

# Self-Hosted Installation Guide

Health Assistant is a self-hosted, open-source health records platform. You own the data and the infrastructure. This guide covers installing it on your own machine or server with Docker; a single command takes you from a fresh clone to a running, configured instance. For running from source with hot-reload, see the [Development Guide](./DEVELOPMENT.md).

## Quick Start — Docker (recommended)

Docker is the fastest way to get up and running. Everything (UI, API, task monitor) is served behind a single built-in Nginx proxy on port 80. You'll need: **Docker** with **Docker Compose** (Linux, WSL2, or macOS), **Git**, and **Python 3** (used only once to generate your secure keys). The stack needs about 2–3 GB of free RAM.

1. **Install Docker and Docker Compose**, then **clone the repository:**

   ```bash
   git clone https://github.com/health-assistant-io/health-assistant.git
   cd health-assistant
   ```

2. **Run the installer:**

   ```bash
   ./scripts/install.sh
   ```

   The installer:
   - checks that Docker is installed and running,
   - generates your `.env` file — it asks **one question** (the public app URL — press Enter on this machine to accept `http://localhost`), generates all secure keys and passwords automatically, and skips advanced options you don't need,
   - starts the standalone stack and waits until the backend reports healthy,
   - prints the URLs to open.

   That's it. `install.sh` is safe to re-run after an update — it never overwrites an existing `.env`.

   > **Prefer explicit steps?** The manual equivalent is `python3 scripts/setup_env.py` (Quick Start is the default), then `docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d`. Or edit `.env` by hand from the template — see [Advanced & Reference](#advanced--reference).

3. **Create your admin account (first-run setup wizard):**

   Open the app URL the installer prints. On a fresh install the app detects that no admin exists and redirects you to the **setup wizard** instead of the login screen. Fill in the **organization name**, and choose an **admin email and password**.

   > **There are no default login credentials.** The email and password you enter in the wizard are the ones you sign in with from then on.

   - **Localhost** installs (`http://localhost`) need **no setup token**.
   - **Domain or LAN** installs get a **one-click setup URL** (`http://your-host/setup?token=…`) printed by the installer — the token is filled in for you; just click.
   - If you used the manual commands instead, retrieve the token from the backend logs: `docker compose --env-file .env -f docker/docker-compose.standalone.yml logs backend | grep -i -A 1 "setup token"`.

4. **Verify it's working:**

   ```bash
   curl http://localhost/health
   # Expected: {"status":"healthy",...}
   ```

   Open the app URL in your browser and you'll see the login screen.

### What the installer configures (and what it doesn't)

Quick Start writes production-oriented settings (`APP_ENV=production`, `DEBUG=false`, `TRUSTED_PROXY_COUNT=1`) aligned with the standalone stack. A few things are intentionally left for later:

- **AI features** — OCR, document extraction, and the chat assistant need an **AI provider key**, configured in-app (System Admin → AI) or via the `OPENAI_*` env vars. The app runs fine without one; you just don't get the AI features.
- **Demo mode, the Flower task monitor, and anatomy expansion packs** exist but aren't needed for a first install — see [Advanced & Reference](#advanced--reference).
- **TLS** is not automated — for an internet-facing domain, add HTTPS after install (see [TLS](#tls)).

## Production Deployment

Before exposing an instance to the internet, review the [Security Checklist](#security-checklist) and the [TLS](#tls) section.

### Deployment Flavors

We provide two production deployment configurations:

#### Flavor 1: Standalone (All-in-One)

**Recommended for fresh VPS deployments.** Includes a fully configured Nginx reverse proxy running in a container. It routes traffic to the internal services and exposes only port 80.

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

*Note: edit `docker/nginx.conf` to set your actual `server_name` instead of the default catch-all `_`.*

#### Flavor 2: Bring-Your-Own-Proxy

**Recommended if you already run a proxy server** (Traefik, Caddy, Nginx Proxy Manager, Cloudflare Tunnel, etc.). Runs the application containers without an internal proxy; `backend`, `frontend`, and `flower` bind securely to `127.0.0.1`. Point your proxy at the frontend (`:3000`) and backend (`:8000`).

```bash
docker compose --env-file .env -f docker/docker-compose.prod.yml up -d
```

### Container images & custom registries

The compose files pull **pre-built images** — there is no `build:` step. By default they come from the GitHub Container Registry:

```
ghcr.io/health-assistant-io/health-assistant/health-assistant-backend:latest
ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend:latest
```

Three `.env` variables redirect the images without editing the compose file:

| Variable | Default | Purpose |
|---|---|---|
| `REGISTRY` | `ghcr.io` | Your own registry or mirror |
| `REPOSITORY` | `health-assistant-io/health-assistant` | Your namespace or fork |
| `IMAGE_TAG` | `latest` | Pin a specific release for reproducible deploys |

**Pin a release** — recommended for production, so an upstream `latest` push can't change your running version. Add to `.env`:

```bash
IMAGE_TAG=0.3.2   # example — use a tag published to your registry (see CHANGELOG.md)
```

**Run from source** — no registry, offline, or a modified build. Build and tag the images locally first; `docker compose up` then reuses them:

```bash
docker build -t ghcr.io/health-assistant-io/health-assistant/health-assistant-backend:latest -f docker/Dockerfile .
docker build -t ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend:latest -f docker/Dockerfile.frontend .
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

### Security Checklist

- [ ] Change `SECRET_KEY` to a secure random value *(handled by `setup_env.py` if used)*
- [ ] **Set `INTEGRATION_SECRET_KEY`** (Fernet key) *(handled by `setup_env.py` if used)*
- [ ] **Set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_ADMIN_EMAIL`** (required for Web Push; the app refuses to boot in production without the keys). Easiest path: `python3 scripts/setup_env.py` generates both keys and prompts for the email automatically. Manual alternative: `npx web-push generate-vapid-keys`, then set `VAPID_ADMIN_EMAIL` to a real address you monitor. *(Optional in development — Web Push is silently skipped when keys are missing.)*
- [ ] **Set `POSTGRES_PASSWORD`** to a strong, unique value *(handled by `setup_env.py` if used)*
- [ ] **Set `FLOWER_USER` and `FLOWER_PASSWORD`** *(handled by `setup_env.py` if used)*
- [ ] **Run the api_key backfill** if upgrading from a pre-0.3.0 release: `cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py`
- [ ] Set `DEBUG=false`
- [ ] Set `APP_ENV=production`
- [ ] Use HTTPS/TLS (terminate at the reverse proxy)
- [ ] Configure firewall rules
- [ ] Set up database backups
- [ ] Rate limiting is **built in** (Redis-backed, per-client-IP on `/auth/login`/`register`/`refresh`/`invite`)
- [ ] Baseline **security headers are automatic** on every response (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS)
- [ ] Enable logging and monitoring (Flower at `/flower` behind the reverse proxy is a good dashboard)
- [ ] **Webhook/API secrets are automatic** — every new integration instance is provisioned with an HMAC secret (shown once at creation); unsigned requests are rejected
- [ ] Set `TRUSTED_PROXY_COUNT` to the number of reverse proxies in front of the app (0 = direct exposure) so rate limiting can't be bypassed with a spoofed `X-Forwarded-For`
- [ ] Set `REDIS_PASSWORD` — the docker stacks require it and Redis runs with `requirepass`
- [ ] API docs (`/docs`) are disabled in production by default; set `ENABLE_API_DOCS=true` only if you understand the exposure

#### TLS

Health data must never cross the network in cleartext. The standalone nginx flavor ships HTTP-only for local/VPN use — for any internet-facing deployment pick ONE:

1. **In-stack TLS (`nginx-TLS.conf`):** copy `docker/nginx-TLS.conf`, replace `SERVER_NAME` with your domain, mount your certificate (`fullchain.pem` + `privkey.pem`) as shown in the file header, and mount it over `nginx.conf`. Obtain the certificate on the host with certbot webroot:

   ```bash
   sudo apt install certbot
   sudo certbot certonly --webroot -w /var/www/certbot -d health.example.com
   ```

2. **Bring-your-own-proxy:** terminate TLS at your existing proxy (Traefik, Caddy, Nginx Proxy Manager, Cloudflare Tunnel) and keep the stack's nginx HTTP-only on an internal network. Set `APP_URL`/`FRONTEND_URL` to the public HTTPS origins (they drive CORS, the OAuth issuer, and TrustedHost validation).

## Updates

### Docker (recommended)

Keep a Docker install up to date with one command:

```bash
./scripts/update-docker.sh
```

It pulls the latest code (best-effort — a dirty tree won't break your running install), refreshes the container images, restarts the stack, and waits for the backend to become healthy again.

Prefer the explicit four-step sequence? The manual equivalent is:

```bash
git pull
python3 scripts/setup_env.py   # only needed when .env.example gained new required vars
docker compose --env-file .env -f docker/docker-compose.standalone.yml pull
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

The application runs its seed pipeline on every boot, so catalog/taxonomy/anatomy entries reconcile automatically — no manual re-seed step after an update. If a release ships a new Alembic migration it applies on container start; check [CHANGELOG.md](../CHANGELOG.md) for any one-time post-upgrade actions (e.g. the `encrypt_existing_api_keys.py` backfill called out in the security checklist).

### From source (development)

For a venv/dev install (see the [Development Guide](./DEVELOPMENT.md)):

```bash
git pull
```

Backend:

```bash
cd backend && source venv/bin/activate && pip install -r requirements.txt
```

Frontend:

```bash
cd frontend && npm install
```

## Troubleshooting

### Port Already in Use

Find what's holding the port (run the one for your port):

```bash
lsof -i :80
```

```bash
lsof -i :8000
```

```bash
lsof -i :3000
```

Then kill it, e.g. for port 80:

```bash
lsof -ti:80 | xargs kill -9
```

### Backend Not Becoming Healthy (first boot)

The installer waits up to three minutes. If it times out, check the backend logs for the cause:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml logs backend --tail=100
```

Common causes: an existing `.env` with stale or placeholder secrets, or a port conflict (see above). Delete `.env` and re-run `./scripts/install.sh` to regenerate clean secrets.

### Backend Import Errors

```bash
cd backend && source venv/bin/activate && python -c "from app.main import app"
```

### Database Connection Error

- Check `DATABASE_URL` in `.env`.
- Ensure PostgreSQL is running: `docker compose --env-file .env -f docker/docker-compose.standalone.yml ps`.
- Remember PostgreSQL needs the **TimescaleDB extension** — a plain Postgres will crash on the telemetry hypertable migration. The compose files ship a compatible image.

## Advanced & Reference

### Manual `.env` setup

If you prefer not to use `setup_env.py`, copy the template and fill in the required values yourself:

```bash
cp .env.example .env
```

You **must** generate your own `SECRET_KEY`, `POSTGRES_PASSWORD`, `FLOWER_PASSWORD`, `REDIS_PASSWORD`, and `INTEGRATION_SECRET_KEY` (a base64url-encoded 32-byte Fernet key), plus the VAPID pair for production, and paste them into the `.env` file. `setup_env.py`'s Keys Only mode (`python3 scripts/setup_env.py --mode=3`) generates just the keys and leaves everything else at the template defaults.

### Setup-token modes

The first-run wizard's token protects a fresh instance from being claimed by a stranger before you do. Four modes exist (see `dev/audits/setup-token-modes.md`):

| Mode | Behaviour | Recommended deploy |
|---|---|---|
| `log` (default) | Backend prints a one-time token to container logs; wizard requires it for non-localhost / non-dev. | Manual Docker installs behind an internet-exposed reverse proxy. |
| `env` | Seed the token from `SETUP_BOOTSTRAP_TOKEN`; the launcher composes the wizard URL with `?token=<value>`. One-click, no log-grep. | Store bundlers (Umbrel/Runtipi/CasaOS/Cosmos) + Ansible/Terraform provisioning. Quick Start chooses this automatically for domain/LAN URLs. |
| `time` | Tokenless for `SETUP_TOKEN_GRACE_MINUTES` (default 30) after first boot, then required (lazy-falls-back to `log` if no env token was set). | LAN/firewalled installs where the operator is the only one who can reach the app in the first half hour. |
| `disabled` | Never require. Logs a security warning on every fresh boot. | Only behind a firewall / VPN / `127.0.0.1` bind — opt-in only. |

Localhost requests and dev/test envs always skip the token in every mode. Store-bundle recipe (env mode):

```env
SETUP_TOKEN_MODE=env
SETUP_BOOTSTRAP_TOKEN=<generated 24-char secret>
```

Launch URL: `https://<your-host>/setup?token=<same value>`. The wizard auto-fills and the user clicks one button.

### Headless / automation alternative

If you're provisioning via Docker/Ansible and can't use a browser, create the admin from the CLI instead:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/create_system_admin.py --email admin@example.com --password securepassword --tenant "My Organization"
```

The `admin@example.com` / `securepassword` values are **placeholders** — replace them. `--password` is **required** (≥8 chars).

### First-run seeding & catalogs

The application runs an ordered seed pipeline on every boot (`SeedService.seed_all()` — see [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md)) that idempotently upserts: **concepts** (taxonomy), diseases, medications, vaccines, clinical event types, allergies, the **anatomy graph** (54 body structures + topology edges), **concept edges** (including specialty→organ links), the **default biomarker catalog** (units + standard lab-test definitions), and **biomarker panels**. No manual action is required for any of these.

The anatomy graph ships as `backend/data/seeds/anatomy_structures.json` (nodes) and `backend/data/seeds/concept_edges.json` (edges) — powering the Anatomy Explorer UI and body-location selection in clinical events.

The standalone `scripts/seed_default_catalog.py` / `scripts/seed_anatomy.py` CLIs can **force a re-seed** outside the startup pipeline, and specialized deployments can import custom anatomy expansion packs — see [Optional: anatomy expansion packs](#optional-anatomy-expansion-packs).

### Optional: anatomy expansion packs

The base anatomy catalog (54 nodes) ships with the app and is seeded automatically on every boot. For specialized deployments (e.g. ophthalmology, neurology) you can import custom anatomy packs:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend \
  python scripts/seed_anatomy.py --file /path/to/my-anatomy-pack.json
```

From a URL:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend \
  python scripts/seed_anatomy.py --url https://example.com/anatomy-pack.json
```

The JSON format:

```json
{
  "nodes": [
    {
      "slug": "left-ventricle",
      "name": "Left Ventricle",
      "category": "ORGAN_PART",
      "standard_system": "snomed",
      "standard_code": "87878005",
      "description": "The lower left chamber of the heart"
    }
  ],
  "edges": [
    { "source_slug": "left-ventricle", "target_slug": "heart", "relation_type": "PART_OF" }
  ]
}
```

Nodes are upserted by `slug` (existing nodes update, nothing is deleted); edges deduplicate on `(source, target, relation_type)`. The REST endpoint `POST /api/v1/anatomy/import` (SYSTEM_ADMIN token) accepts the same payload for programmatic imports. You can also ask the AI Assistant to generate a sub-graph on demand (e.g. *"generate the detailed anatomy of the cardiovascular system"*) — it produces a human-in-the-loop review card with an editable node/edge table for your approval before anything is imported. See [Seeding & Demo Data §6.4](./SEEDING_AND_DEMOS.md#64-ai-driven-graph-expansion) for full details.

## See also

- [Getting Started Guide](./GETTING_STARTED_GUIDE.md) — first-hour walkthrough after install (add a person, upload a lab, configure AI, connect a wearable)
- [Architecture Overview](./ARCHITECTURE.md) — tech stack, data model, biomarker engine, AI pipeline
- [Development Guide](./DEVELOPMENT.md) — local dev setup with hot-reload
- [Seeding & Demo Data](./SEEDING_AND_DEMOS.md) — how catalogs, taxonomy, and anatomy reconcile on boot
- [Tenancy & User Management](./TENANCY_AND_USER_MANAGEMENT.md) — tenants, roles, invite tokens
- [CHANGELOG.md](../CHANGELOG.md) — recent updates and any post-upgrade actions
# Self-Hosted Installation Guide

Health Assistant is a self-hosted, open-source health records platform. You own the data and the infrastructure. This guide covers installing it on your own machine or server with Docker; a single command takes you from a fresh clone to a running, configured instance. For running from source with hot-reload, see the [Development Guide](./DEVELOPMENT.md).

## Quick Start — Docker (recommended)

Docker is the fastest way to get up and running. Everything (UI, API, task monitor) is served behind a single built-in Nginx proxy on port 80. You'll need: **Docker** with **Docker Compose** (Linux, WSL2, or macOS), **Git**, and **Python 3** (used only once to generate your secure keys). The stack needs about 2–3 GB of free RAM.

### One-line install (no clone needed)

If you'd rather not clone anything, the remote bootstrap fetches the latest
published release, shows you its SHA-256, extracts it to `~/health-assistant`,
and hands off to the same installer:

```bash
curl -fsSL https://health-assistant.io/install.sh | bash
```

The script is small and readable on purpose — read it before running it
(it is also in the repo at `scripts/bootstrap.sh`). It never runs anything as
root and never installs system packages; if Docker is missing it prints
install instructions instead. Pin a release or relocate the install with
environment variables or flags (`HEALTH_REF`, `HEALTH_INSTALL_DIR`,
`HEALTH_EXPECTED_SHA256`, `--dry-run`, `--dir`, `--ref` — see `--help`).
Everything after the flags is forwarded to `scripts/install.sh`:

```bash
curl -fsSL https://health-assistant.io/install.sh | bash -s -- --dir /srv/ha
```

The clone-based flow below does exactly the same thing and stays the source
of truth — the one-liner is a convenience wrapper around it.

Re-running that command is safe: when the install directory already holds an
install, the bootstrap skips the download and hands off to that install's own
`scripts/install.sh` — which keeps `.env`, refreshes the images, restarts the
stack, and waits for the health check. Images are **pinned to the release the
tarball came from** (`v0.9.0` → `IMAGE_TAG=0.9.0`), because `:latest` is built
from `main`; a release tree running main images fails at the database
migration step.

### Clone-based install

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
   - checks every host port the stack publishes and names the `.env` variable
     to change when one is taken (``--auto-ports`` picks free ports for you),
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

Quick Start writes production-oriented settings (`HA_APP_ENV=production`, `DEBUG=false`, `HA_TRUSTED_PROXY_COUNT=1`) aligned with the standalone stack. A few things are intentionally left for later:

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
IMAGE_TAG=0.9.0   # example — use a tag published to your registry (see CHANGELOG.md)
```

**Run from source** — no registry, offline, or a modified build. Build and tag the images locally first; `docker compose up` then reuses them:

```bash
docker build -t ghcr.io/health-assistant-io/health-assistant/health-assistant-backend:latest -f docker/Dockerfile .
docker build -t ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend:latest -f docker/Dockerfile.frontend .
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

### Security Checklist

> The full picture — threat model per surface (auth, sessions, the
> machine/device HMAC bridge, secrets at rest, audit, deployment) — lives in
> [SECURITY.md](../SECURITY.md); this checklist is the operator-side
> minimum.

- [ ] **Set the per-purpose key family** *(handled by `setup_env.py` if used)* — three independent per-purpose secrets (the single all-purpose `SECRET_KEY` is retired):
  - `HA_SESSION_KEY` (signs session JWTs + api/invite/download kinds): `python3 -c "from secrets import token_urlsafe; print(token_urlsafe(48))"`
  - `HA_REFRESH_KEY` (signs refresh JWTs only): same command, a **different** value — boot refuses shared/partial/weak pins
  - `HA_DATA_KEY` (Fernet at-rest key; never signs): `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — pre-H4 deployments rename their `INTEGRATION_SECRET_KEY` value here (same value, every sealed ring keeps decrypting); put a retired key in `HA_DATA_KEY_PREVIOUS` when rotating. The at-rest cipher implementation is the shared [`neuronection-auth-kit`](https://github.com/neuronection/auth-kit) module (`nx_auth.atrest`), pinned by git SHA in the workspace `pyproject.toml`/`uv.lock`.
- [ ] **Set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_ADMIN_EMAIL`** (required for Web Push; the app refuses to boot in production without the keys). Easiest path: `python3 scripts/setup_env.py` generates both keys and prompts for the email automatically. Manual alternative: `npx web-push generate-vapid-keys`, then set `VAPID_ADMIN_EMAIL` to a real address you monitor. *(Optional in development — Web Push is silently skipped when keys are missing.)*
- [ ] **Set `POSTGRES_PASSWORD`** to a strong, unique value *(handled by `setup_env.py` if used)*
- [ ] **Set `FLOWER_USER` and `FLOWER_PASSWORD`** *(handled by `setup_env.py` if used)*
- [ ] **Run the api_key backfill** if upgrading from a pre-0.3.0 release: `cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py`
- [ ] **Two-factor authentication (TOTP MFA)** — no extra setup: it uses the `HA_DATA_KEY` above to encrypt the per-user authenticator secrets at rest (losing/rotating that key without `HA_DATA_KEY_PREVIOUS` would break existing enrollments). Users self-enroll from **Settings → Security** (any authenticator app; recovery codes are shown once). To *require* MFA for institute members: Admin → Users → row action **Require MFA** (or `PATCH /api/v1/admin/tenants/{tenant_id}/users/{user_id}/mfa` with `{"enforced": true}`) — the member then enrolls at their next sign-in. Wrong codes count toward the normal 5-strike account lockout.
- [ ] Set `DEBUG=false`
- [ ] Set `HA_APP_ENV=production`
- [ ] Use HTTPS/TLS (terminate at the reverse proxy)
- [ ] Configure firewall rules
- [ ] Set up database backups — built in: `scripts/backup.sh` / `scripts/restore.sh`, plus the in-app [Export & Import](./EXPORT_IMPORT.md) (FHIR Bundle + ZIP with SHA256 manifest)
- [ ] Rate limiting is **built in** (Redis-backed, per-client-IP on `/auth/login`/`register`/`refresh`/`invite`)
- [ ] Baseline **security headers are automatic** on every response (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, a `Content-Security-Policy`, HSTS); the SPA document gets the same policy from the frontend image (`public/serve.json` → `serve`). If you front the SPA with your own proxy, pass those headers through — do not replace them with a weaker policy
- [ ] Enable logging and monitoring (Flower at `/flower` behind the reverse proxy is a good dashboard)
- [ ] **Webhook/API secrets are automatic** — every new integration instance is provisioned with an HMAC secret (shown once at creation); unsigned requests are rejected
- [ ] Set `HA_TRUSTED_PROXY_COUNT` to the number of reverse proxies in front of the app (0 = direct exposure) so rate limiting can't be bypassed with a spoofed `X-Forwarded-For`
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

The application runs its seed pipeline on every boot, so catalog/taxonomy/anatomy entries reconcile automatically — no manual re-seed step after an update. If a release ships a new Alembic migration it applies on container start; check [CHANGELOG.md](../CHANGELOG.md) for any one-time post-upgrade actions (e.g. the `encrypt_existing_api_keys.py` backfill called out in the security checklist). v0.9.0 is the exception: its migration chain was squash-replaced, so a pre-0.9 database is refused — see [Upgrading from v0.8.0](#upgrading-from-v080).

### Upgrading from v0.8.0

v0.9.0 is a breaking release for existing installations. It replaced the
published migration chain with a single `0001_baseline`, renamed the
environment namespace, and moved to PostgreSQL 16. **There is no in-place
upgrade from v0.8.0.** The migrate service refuses a database created by an
older release, and `./scripts/upgrade-check.sh` reports the same thing before
anything is pulled:

```text
This database was created by Health Assistant v0.8.0 or earlier. v0.9.0
replaced the migration chain with 0001_baseline, so the recorded revision(s)
... no longer exist and an in-place upgrade is not supported.
```

Pick one route:

**1. No data to keep** (a demo or throwaway install): back up `.env`, then

```bash
./scripts/install.sh --reset --yes
```

**2. Keep your data** — back up first (`./scripts/backup.sh`), then either:

- **Clinical data:** export from the running v0.8.0 instance
  (Export & Import → FHIR Bundle or ZIP), start a fresh v0.9.0 install, and
  import the bundle. This is the supported data-preserving route.
- **Full database:** a v0.8.0 dump cannot be restored into the v0.9.0 schema
  as-is (tables, columns, types and constraints changed). Copy rows
  table-by-table into a fresh v0.9.0 database after it has migrated; there is
  no automated transform for the pre-0.9 shape.

Environment names renamed in v0.9.0 (a stale name is ignored; the
`[blocked]`/`[warn]` output of `upgrade-check.sh` names each replacement):

| v0.8.0 | v0.9.0 |
|---|---|
| `APP_ENV` | `HA_APP_ENV` |
| `SECRET_KEY` | `HA_SESSION_KEY` + `HA_REFRESH_KEY` + `HA_DATA_KEY` (separate keys) |
| `INTEGRATION_SECRET_KEY` | `HA_DATA_KEY` — **same Fernet value** so sealed credentials and MFA stay decryptable |
| `INTEGRATION_SECRET_KEY_PREVIOUS` | `HA_DATA_KEY_PREVIOUS` |
| `DATABASE_URL` | `HA_DATABASE_URL` |
| `UPLOAD_DIR` | `HA_UPLOAD_DIR` |
| `TRUSTED_PROXY_COUNT` | `HA_TRUSTED_PROXY_COUNT` |
| `DEMO_MODE` / `DEMO_USER_EMAIL` / `DEMO_USER_PASSWORD` | `HA_DEMO_MODE` / `HA_DEMO_EMAIL` / `HA_DEMO_PASSWORD` |
| `JWT_EXPIRATION_HOURS` | `HA_AUTH_ACCESS_TTL_MINUTES` (minutes, not hours) |
| `OAUTH_ISSUER`, `AI_CHECKPOINT_RETENTION_DAYS` | retired |

The PostgreSQL data directory is also not compatible (v0.8.0 ran pg14,
v0.9.0 runs pg16) — see [`docker/README.md` → "Upgrading from pg14"](../docker/README.md#upgrading-from-pg14).

Re-running the one-line installer repairs the image pin and the `.env`
contract; it does **not** migrate a pre-0.9 database.

### From source (development)

For a venv/dev install (see the [Development Guide](./DEVELOPMENT.md)):

```bash
git pull
```

Backend:

```bash
cd backend && uv sync
```

Frontend:

```bash
cd frontend && pnpm install
```

## Troubleshooting

### Port Already in Use

The installer checks every published host port **before** it pulls or starts
anything, and names the `.env` variable to change:

```
Port conflict — the stack cannot start:
  redis: host port 6379 is in use → set REDIS_PORT=<free port> in .env
```

| Port | Variable | Default |
|---|---|---|
| 80 | `HTTP_PORT` | 80 — the single public port (nginx) |
| 5432 | `POSTGRES_PORT` | 5432 (loopback) |
| 6379 | `REDIS_PORT` | 6379 (loopback) |
| 8000 | `BACKEND_PORT` | 8000 (loopback) |
| 3000 | `FRONTEND_PORT` | 3000 (loopback) |
| 5555 | `FLOWER_PORT` | 5555 (loopback) |

Change the variable in `.env` and re-run `./scripts/install.sh` (it reuses your
`.env`), or let the installer pick free ports itself:

```bash
./scripts/install.sh --auto-ports
```

Find what is holding a port:

```bash
ss -ltnp | grep ':6379'
```

```bash
lsof -i :6379
```

If the message says the port is a *literal* in
`docker/docker-compose.standalone.yml`, edit it there — older release tarballs
hardcode the gateway's port.

### Backend Not Becoming Healthy (first boot)

The installer waits up to three minutes, and it stops early when a service has
already exited: it prints the state of every container plus the last log lines
of the failed ones. When it times out instead, inspect the backend and the
migration step:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml logs backend --tail=100
```

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml logs migrate --tail=50
```

Common causes: a port conflict (see above — normally caught before startup), or
a database volume left behind by a previous install whose password no longer
matches `.env` (the installer offers to reset it). Deleting `.env` is not the
fix — it discards the keys and credentials the data was created with.

### Backend Import Errors

```bash
cd backend && python -c "from app.main import app"
```

### Database Connection Error

- Check `HA_DATABASE_URL` (or the `POSTGRES_*` parts) in `.env`.
- Ensure PostgreSQL is running: `docker compose --env-file .env -f docker/docker-compose.standalone.yml ps`.
- Remember PostgreSQL needs the **TimescaleDB extension** — a plain Postgres will crash on the telemetry hypertable migration. The compose files ship a compatible image.

## Advanced & Reference

### Manual `.env` setup

If you prefer not to use `setup_env.py`, copy the template and fill in the required values yourself:

```bash
cp .env.example .env
```

You **must** generate your own `HA_SESSION_KEY` and `HA_REFRESH_KEY` (independent random values, ≥32 chars), `POSTGRES_PASSWORD`, `FLOWER_PASSWORD`, `REDIS_PASSWORD`, and `HA_DATA_KEY` (a base64url-encoded 32-byte Fernet key), plus the VAPID pair for production, and paste them into the `.env` file. `setup_env.py`'s Keys Only mode (`python3 scripts/setup_env.py --mode=3`) generates just the keys and leaves everything else at the template defaults.

> **Key separation note:** `HA_SESSION_KEY` signs session-family JWTs, `HA_REFRESH_KEY` signs refresh JWTs only, and `HA_DATA_KEY` is the Fernet key for secrets at rest — no key is derived from another and no value may serve two purposes (the boot guard refuses). Verification is family-locked, so a token signed by the "wrong" key fails — rotating the session key invalidates only session tokens, and the data key never affects login. `HA_DATA_KEY_PREVIOUS` (comma-separated) holds prior Fernet keys accepted for decryption only, so rotating the at-rest key never orphans stored ciphertext: new writes seal under the primary, old values keep decrypting until re-sealed.

### Setup-token modes

The first-run wizard's token protects a fresh instance from being claimed by a stranger before you do. Four modes exist:

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
- [Architecture Overview](./ARCHITECTURE.md) — system diagram, data model, biomarker engine, AI pipeline
- [Export & Import (Backup)](./EXPORT_IMPORT.md) — built-in FHIR/ZIP backup and restore
- [Development Guide](./DEVELOPMENT.md) — local dev setup with hot-reload
- [Seeding & Demo Data](./SEEDING_AND_DEMOS.md) — how catalogs, taxonomy, and anatomy reconcile on boot
- [Tenancy & User Management](./TENANCY_AND_USER_MANAGEMENT.md) — tenants, roles, invite tokens
- [CHANGELOG.md](../CHANGELOG.md) — recent updates and any post-upgrade actions
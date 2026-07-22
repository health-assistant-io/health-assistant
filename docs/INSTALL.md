# Health Assistant — Self-Hosted Installation Guide

## Quick Start (Recommended)

Docker is the fastest way to get Health Assistant — a self-hosted, open-source health records platform — up and running. This quick start takes you from a fresh clone to a working install in seven steps: clone, generate secure keys, boot the stack, create your admin account, and sign in. Everything (UI, API, API docs) is served behind a single Nginx proxy on port 80.

### Setup

1. **Install Docker** and Docker Compose on your system.
2. **Clone the repository:**
   ```bash
   git clone https://github.com/health-assistant-io/health-assistant.git
   ```

   ```bash
   cd health-assistant
   ```

3. **Initialize environment & secure keys:**
   **Option A: Interactive Setup (Recommended)**
   Run the setup script to copy the template to `.env`, **automatically generate secure passwords and cryptographic keys**, and interactively configure your environment settings (URLs, workers, debug mode, Web Push contact email):
   ```bash
   python scripts/setup_env.py
   ```
   The script generates: `SECRET_KEY`, `INTEGRATION_SECRET_KEY` (Fernet), `POSTGRES_PASSWORD`, `FLOWER_PASSWORD`, and a VAPID P-256 key pair (`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`). In Full Setup mode it also prompts for `VAPID_ADMIN_EMAIL` with a smart default derived from the APP_URL you enter (e.g. `https://health.example.com` → suggests `admin@health.example.com`).

   **Option B: Manual setup**
   If you cannot run the Python script, copy the template manually:
   ```bash
   cp .env.example .env
   ```
   *Note: If you choose manual setup, you **must** generate your own `SECRET_KEY`, `POSTGRES_PASSWORD`, `FLOWER_PASSWORD`, and `INTEGRATION_SECRET_KEY` (a base64url-encoded 32-byte Fernet key) and manually paste them into the `.env` file.*

4. **Configure remaining settings:**
   Open the newly created `.env` file in your preferred text editor. While your secure keys are set (if you used Option A), you should review and adjust other configurations like ports, `APP_URL`, or optional settings (email, AI, etc.) according to your environment.

5. **Start the application:**
   ```bash
   docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
   ```
   *(Note: This uses the recommended "Standalone" flavor with a built-in Nginx proxy on port 80. If you already run a reverse proxy like Traefik/Nginx, or if you want to set up a development environment, see the advanced sections below).*

6. **Create your admin account (first-run wizard):**
   Open [http://localhost](http://localhost) in your browser. On a fresh install the app detects that no admin exists and redirects you to the **setup wizard** instead of the login screen. Fill in:

   - **Organization name** (the initial tenant — e.g. "My Organization"),
   - **Admin email** and **password** (you choose these — there are no defaults),
   - **Setup token**, *only if prompted*. This token prints to the backend container logs on first boot and is required for remote (non-localhost) access to prevent a stranger claiming your instance before you do. Localhost and dev deployments skip it.

   Retrieve the token with:

   ```bash
   docker compose --env-file .env -f docker/docker-compose.standalone.yml logs backend | grep -i "setup token"
   ```

   On submit, the wizard creates your `SYSTEM_ADMIN` account + tenant and logs you straight in.

   > **There are no default login credentials.** The email and password you enter in the wizard are the ones you sign in with from then on.

   **Headless / automation alternative.** If you're provisioning via Docker/Ansible and can't use a browser, create the admin from the CLI instead:

   ```bash
   docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/create_system_admin.py --email admin@example.com --password securepassword --tenant "My Organization"
   ```

   The `admin@example.com` / `securepassword` values are **placeholders** — replace them. Running the script with no flags falls back to `sysadmin@health-assistant.local` / `admin123` (fine for a throwaway local VM, **never** for anything exposed).

   **Clinical Catalogs (auto-seeded on every startup):**
   The application runs a single ordered seed pipeline on boot (`SeedService.seed_all()` — see [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md)) that idempotently upserts: **concepts** (taxonomy, first), diseases, medications, vaccines, clinical event types, allergies, **anatomy graph** (54 body structures + topology edges), **concept edges** (including specialty→organ links), the **default biomarker catalog** (units + standard lab-test definitions), and **biomarker panels**. No manual action is required for any of these — they reconcile to the JSON seed files on every start.

   The anatomy graph ships as `backend/data/seeds/anatomy_structures.json` (nodes) and `backend/data/seeds/concept_edges.json` (edges, including anatomy hierarchy edges) — powering the Anatomy Explorer UI and body-location selection in clinical events.

   The standalone `scripts/seed_default_catalog.py` / `scripts/seed_anatomy.py` CLIs are still available if you ever need to **force a re-seed** outside the startup pipeline, but they are no longer required for first-time setup. Specialized deployments can also import custom anatomy expansion packs — see [Optional: anatomy expansion packs](#optional-anatomy-expansion-packs) at the end of this guide.

7. **Access the application:**
   Once the services are running, open your web browser and navigate to:
   - **Application (frontend UI):** [http://localhost](http://localhost) — this is what you'll use day-to-day. The standalone stack serves the UI, the API, and the API docs behind a single Nginx proxy on port 80.
   - **API docs (Swagger):** [http://localhost/docs](http://localhost/docs) — interactive developer reference for the backend REST API.
   - **Health check:** [http://localhost/health](http://localhost/health) — returns `{"status":"healthy",...}`.
   - **Flower (task monitor):** [http://localhost/flower/](http://localhost/flower/) — Celery worker dashboard (behind the same proxy).

   > Using the **bring-your-own-proxy** flavor instead? The UI/API aren't exposed publicly by default — they bind to `127.0.0.1`. Point your reverse proxy at the frontend (`:3000`) and backend (`:8000`) ports as described under [Production Deployment](#production-deployment). The `:3000` / `:8000` ports below only apply to the **development setup** (see [Development Guide](./DEVELOPMENT.md)).

---

## First-Time Sign-In

There are **two ways** the first admin account is created. You only need one.

**Path A — the setup wizard (what this guide uses).** Step 6 above: open the URL, the app detects the fresh install, and the wizard creates your `SYSTEM_ADMIN` + tenant. You're logged in immediately after.

**Path B — the CLI script (headless/automation).** `create_system_admin.py` (shown in step 6's callout) writes the admin + tenant directly to the database. Useful for Docker/Ansible provisioning where no browser is available.

Once signed in, additional users join by **invite token** — an admin mints one via the in-app user management or `POST /auth/invite`, and the invitee registers with it. Open self-sign-up is disabled by design. See the [Tenancy & User Management guide](./TENANCY_AND_USER_MANAGEMENT.md) for the invite flow.

You can optionally **link your user account to a Patient record** (so the dashboard and biomarker trends show your own data) or to a Doctor record (for clinical staff) from **Profile** in the app. For the full first-hour walkthrough — adding a person, uploading a lab report, configuring the AI, connecting a wearable — see the [Getting Started Guide](./GETTING_STARTED_GUIDE.md).

## Development Setup

If you are looking to contribute to the codebase or run the application from source with hot-reloading, please see our dedicated [Development Guide](./DEVELOPMENT.md) instead of this installation manual.

## Verification

Once running via the standalone setup (Option A), Nginx exposes the application on port 80. You can test it using the following commands:

### Test Backend

```bash
curl http://localhost/health
# Expected: {"status":"healthy","database":"connected","redis":"not_configured"}
```

> The `redis` field reports `not_configured` because the health handler
> doesn't probe Redis directly — it's a static string. Redis connectivity
> is exercised indirectly through the Celery worker and WebSocket paths.
> If the worker is up (check Flower at `/flower/`), Redis is fine.

### Test Frontend

Open http://localhost in your browser. You should see the login screen.

## Production Deployment

When deploying to production, modify the variables within your `.env` file to ensure the system is secure:

- Update `APP_ENV` to `production`
- Update `DEBUG` to `false`
- Update `DATABASE_URL` and `REDIS_URL` to point to your production instances rather than `localhost` (if using external databases).

### Deployment Flavors

We provide two different production deployment configurations depending on your infrastructure setup:

#### Flavor 1: Standalone (All-in-One)
**Recommended for fresh VPS deployments.** 
This flavor includes a fully configured Nginx reverse proxy running inside a Docker container. It routes traffic securely to the internal services and exposes only port 80 to the public web.

```bash
# Start the standalone production stack
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```
*Note: Before running, you may want to edit `docker/nginx.conf` to set your actual `server_name` instead of the default catch-all `_`.*

#### Flavor 2: Bring-Your-Own-Proxy
**Recommended if you already run a proxy server (Traefik, Nginx Proxy Manager, Cloudflare Tunnel, etc.).**
This flavor runs the application containers without an internal proxy. By default, the `backend`, `frontend`, and `flower` services bind securely to `127.0.0.1` on the host machine to prevent direct external access. You are responsible for configuring your proxy to route traffic to these local ports.

```bash
# Start the bring-your-own-proxy production stack
docker compose --env-file .env -f docker/docker-compose.prod.yml up -d
```

### Container images & custom registries

The compose files pull **pre-built images** — they have no `build:` step, so `docker compose up` fetches images from a registry rather than building locally. This applies to every flavor above and to the quick start. By default the images come from the public GitHub Container Registry:

```
ghcr.io/health-assistant-io/health-assistant/health-assistant-backend:latest
ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend:latest
```

Three environment variables (set in `.env`) redirect the images without editing the compose file:

| Variable | Default | Purpose |
|---|---|---|
| `REGISTRY` | `ghcr.io` | Point at your own registry or mirror (a private registry, an air-gapped mirror, etc.) |
| `REPOSITORY` | `health-assistant-io/health-assistant` | Your namespace or fork |
| `IMAGE_TAG` | `latest` | Pin a specific release for reproducible deploys |

**Pin a release** — recommended for production, so an upstream `latest` push can't change your running version. Add to `.env`:

```bash
IMAGE_TAG=0.3.2   # example — use a tag published to your registry (see CHANGELOG.md)
```

**Use your own registry** — a fork that publishes its own images, or an internal mirror. Add to `.env`:

```bash
REGISTRY=registry.yourdomain.tld
REPOSITORY=myorg/health-assistant
```

**Run from source** — no registry, offline, or a modified build. Since the compose files only pull, build and tag the images locally first; `docker compose up` then reuses the local images instead of pulling:

```bash
docker build -t ghcr.io/health-assistant-io/health-assistant/health-assistant-backend:latest -f docker/Dockerfile .
```

```bash
docker build -t ghcr.io/health-assistant-io/health-assistant/health-assistant-frontend:latest -f docker/Dockerfile.frontend .
```

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

### Security Checklist

- [ ] Change `SECRET_KEY` to a secure random value *(handled by `setup_env.py` if used)*
- [ ] **Set `INTEGRATION_SECRET_KEY`** (Fernet key) *(handled by `setup_env.py` if used)*
- [ ] **Set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_ADMIN_EMAIL`** (required for Web Push; the app refuses to boot in production without the keys). Easiest path: `python scripts/setup_env.py` generates both keys and prompts for the email automatically. Manual alternative: `npx web-push generate-vapid-keys` for the key pair, then set `VAPID_ADMIN_EMAIL` to a real address you monitor (push services use it to contact you about delivery issues). *(Optional in development — Web Push is silently skipped when keys are missing.)*
- [ ] **Set `POSTGRES_PASSWORD`** to a strong, unique value *(handled by `setup_env.py` if used)*
- [ ] **Set `FLOWER_USER` and `FLOWER_PASSWORD`** *(handled by `setup_env.py` if used)*
- [ ] **Run the api_key backfill** if upgrading from a pre-0.3.0 release: `cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py`
- [ ] Set `DEBUG=false`
- [ ] Set `APP_ENV=production`
- [ ] Use HTTPS/TLS (terminate at the reverse proxy)
- [ ] Configure firewall rules
- [ ] Set up database backups
- [ ] Rate limiting is **built in** (Redis-backed, per-client-IP on `/auth/login`/`register`/`refresh`/`invite`) — just ensure Redis is reachable; it degrades open if Redis is down (audit A2)
- [ ] Baseline **security headers are automatic** on every response (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS) (audit A7); tighten `APP_CSP_CONTENT` if you serve the SPA from the backend origin
- [ ] Enable logging and monitoring (Flower at `/flower` behind the reverse proxy is a good dashboard)
- [ ] **Set webhook secrets** for any integrations that receive webhooks — add `webhook_secret` to each integration's `user_config`; the sender must sign payloads with `HMAC-SHA256`

#### TLS with Let's Encrypt

The standalone stack terminates TLS at Nginx. The easiest way to add a real certificate on the host in front of the stack is `certbot`:

```bash
sudo apt install certbot python3-certbot-nginx
```

```bash
sudo certbot --nginx -d health.example.com
```

If you run the **bring-your-own-proxy** flavor, terminate TLS at your existing proxy (Traefik, Caddy, Nginx Proxy Manager, Cloudflare Tunnel) instead — the app containers don't need to know about certificates.

## Updates

### Docker (recommended)

This is the update path for the standalone / prod compose stacks described above:

```bash
git pull
```

```bash
python scripts/setup_env.py   # only needed when .env.example gained new required vars
```

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml pull
```

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

The application runs its seed pipeline on every boot, so new catalog/taxonomy/anatomy entries reconcile automatically — no manual re-seed step after an update. If a release ships a new Alembic migration it applies on container start; check [CHANGELOG.md](../CHANGELOG.md) for any one-time post-upgrade actions (e.g. the `encrypt_existing_api_keys.py` backfill called out in the security checklist).

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

Then kill it, e.g. for port 8000:

```bash
lsof -ti:8000 | xargs kill -9
```

### Backend Import Errors

```bash
cd backend
```

```bash
source venv/bin/activate
```

```bash
python -c "from app.main import app"   # should print nothing (no errors)
```

### Frontend Build Errors

```bash
cd frontend
```

```bash
npm run build   # surfaces TypeScript / ESLint errors
```

### Database Connection Error

- Check `DATABASE_URL` in `.env`.
- Ensure PostgreSQL is running: `systemctl status postgresql` (or `docker compose … ps`).
- Verify the database exists: `psql -U admin -l`.
- Remember PostgreSQL needs the **TimescaleDB** extension — a plain Postgres will crash on the telemetry hypertable migration. The compose files ship a compatible image.

## Optional: anatomy expansion packs

The base anatomy catalog (54 nodes) ships with the app and is seeded automatically on every boot. For specialized deployments (e.g. ophthalmology, neurology) you can import custom anatomy packs.

From a local file:

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

Nodes are upserted by `slug` (existing nodes update, nothing is deleted); edges deduplicate on `(source, target, relation_type)`. The REST endpoint `POST /api/v1/anatomy/import` (SYSTEM_ADMIN token) accepts the same payload for programmatic imports. You can also ask the AI Assistant to generate a sub-graph on demand (e.g. *"generate the detailed anatomy of the cardiovascular system"*) — it produces a human-in-the-loop review card for your approval before anything is imported. See [Seeding & Demo Data](./SEEDING_AND_DEMOS.md) for full details.

## See also

- [Getting Started Guide](./GETTING_STARTED_GUIDE.md) — first-hour walkthrough after install (add a person, upload a lab, configure AI, connect a wearable)
- [Architecture Overview](./ARCHITECTURE.md) — tech stack, data model, biomarker engine, AI pipeline
- [Development Guide](./DEVELOPMENT.md) — local dev setup with hot-reload
- [Seeding & Demo Data](./SEEDING_AND_DEMOS.md) — how catalogs, taxonomy, and anatomy reconcile on boot
- [Tenancy & User Management](./TENANCY_AND_USER_MANAGEMENT.md) — tenants, roles, invite tokens
- [CHANGELOG.md](../CHANGELOG.md) — recent updates and any post-upgrade actions
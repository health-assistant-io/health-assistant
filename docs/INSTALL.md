# Health Assistant - Installation Guide

## Installations - Quickstart (Recommended)

Using Docker is the easiest and most recommended way to get Health Assistant up and running.

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

6. **First-Time Data Seeding:**
   You must manually create your initial admin account:
   
   For Standalone (all-in-one with proxy):
   ```bash
   docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/create_system_admin.py --email admin@example.com --password securepassword --tenant "My Organization"
   ```
   
   Or for Prod (bring-your-own-proxy):
   ```bash
   docker compose --env-file .env -f docker/docker-compose.prod.yml exec backend python scripts/create_system_admin.py --email admin@example.com --password securepassword --tenant "My Organization"
   ```

   **Clinical Catalogs (auto-seeded on every startup):**
   The application runs a single ordered seed pipeline on boot (`SeedService.seed_all()` — see [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md)) that idempotently upserts: medications, clinical event types, allergies, **anatomy graph** (54 body structures + 62 topology edges), the unified **taxonomy** (concepts + concept_edges, including specialty→organ links), and the **default biomarker catalog** (units + standard lab-test definitions). No manual action is required for any of these — they reconcile to the JSON seed files on every start.

   The anatomy graph ships as `backend/data/seeds/anatomy_structures.json` (nodes) and `backend/data/seeds/concept_edges.json` (edges, including anatomy hierarchy edges) — powering the Anatomy Explorer UI and body-location selection in clinical events.

   The standalone `scripts/seed_default_catalog.py` / `scripts/seed_anatomy.py` CLIs are still available if you ever need to **force a re-seed** outside the startup pipeline, but they are no longer required for first-time setup.

   **Anatomy Graph Expansion Packs (Optional):**
   The base anatomy catalog (54 nodes) ships with the application and is seeded automatically. For specialized deployments (e.g., ophthalmology clinics, neurology practices), you can import custom anatomy expansion packs:

   ```bash
   # Standalone (all-in-one with proxy)
   docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/seed_anatomy.py --file /path/to/my-anatomy-pack.json

   # Or from a URL
   docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/seed_anatomy.py --url https://example.com/anatomy-pack.json
   ```

   You can also re-seed the base catalog at any time:
   ```bash
   docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/seed_anatomy.py
   ```

   The JSON format is:
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
       {
         "source_slug": "left-ventricle",
         "target_slug": "heart",
         "relation_type": "PART_OF"
       }
     ]
   }
   ```

   Nodes are upserted by `slug` (existing nodes are updated, never deleted). Edges are deduplicated by `(source, target, relation_type)`. The REST API endpoint `POST /api/v1/anatomy/import` (SYSTEM_ADMIN token) accepts the same JSON format if you prefer to import programmatically. See [Seeding and Demos](./SEEDING_AND_DEMOS.md) for full details.

   You can also ask the AI Assistant to generate anatomy sub-graphs on demand (e.g., "Generate the detailed anatomy of the cardiovascular system"). The AI will create a human-in-the-loop review card with the proposed nodes and edges for your approval before importing.

7. **Access the application:**
   Once the services are running, open your web browser and navigate to:
   - **Main Application (Frontend):** [http://localhost:3000](http://localhost:3000) - *This is the main user interface where you will interact with the Health Assistant.*
   - **API Documentation (Backend):** [http://localhost:8000/docs](http://localhost:8000/docs) - *Interactive developer documentation for the backend API.*

---

## First-Time Setup & Linking

After your application is running and your data is seeded, you need to finalize your user setup.

1. **Log In**: Open the application at [http://localhost](http://localhost) (or your domain) and log in with the credentials you provided to the script.
2. **Auto-Provisioning**: For home users, the system will automatically create a new **Household Tenant** and a **Default Organization** if they don't exist.
3. **Link Your Profile**: Visit your profile settings in the app to link your User account to a Patient or Doctor record.

For more details on managing multiple users and clinical hierarchies, see the [Tenancy and User Management Guide](./TENANCY_AND_USER_MANAGEMENT.md).

## Development Setup

If you are looking to contribute to the codebase or run the application from source with hot-reloading, please see our dedicated [Development Guide](./DEVELOPMENT.md) instead of this installation manual.

## Configuration

Both the frontend and backend utilize `.env` files to document required configuration variables. 

1. Ensure you have run the setup script (or manually copied the example file) to create your active `.env` file:
   ```bash
   python scripts/setup_env.py
   ```
2. Open this file in your preferred text editor.
3. Review the inline comments within the `.env` file and supply any additional specific configuration. If you used the setup script, your secure keys have already been populated. If you chose manual setup, you must ensure all secure keys are generated and filled in.



## Verification

Once running via the standalone setup (Option A), Nginx exposes the application on port 80. You can test it using the following commands:

### Test Backend

```bash
curl http://localhost/health
# Expected: {"status":"healthy","database":"connected","redis":"connected"}
```

### Test Frontend

Open http://localhost in your browser.

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
- [ ] Configure rate limiting
- [ ] Enable logging and monitoring (Flower at `/flower` behind the reverse proxy is a good dashboard)
- [ ] **Set webhook secrets** for any integrations that receive webhooks — add `webhook_secret` to each integration's `user_config`; the sender must sign payloads with `HMAC-SHA256`

```bash
sudo apt install certbot python3-certbot-nginx
```

```bash
sudo certbot --nginx -d health_assistant.example.com
```

## Updates

### Manual Update

**Backend:**
```bash
cd backend
```
```bash
git pull
```
```bash
source venv/bin/activate
```
```bash
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
```
```bash
git pull
```
```bash
npm install
```

## Troubleshooting

### Port Already in Use

Find and kill process on port 8000:
```bash
lsof -i :8000
```
```bash
lsof -ti:8000 | xargs kill -9
```

Find and kill process on port 3000:
```bash
lsof -i :3000
```
```bash
lsof -ti:3000 | xargs kill -9
```

### Backend Import Errors

```bash
cd backend
```
```bash
source venv/bin/activate
```
```bash
python -c "from app.main import app"
```
*(Should output no errors)*

### Frontend Build Errors

```bash
cd frontend
```
```bash
npm run build
```
*(Check for TypeScript/ESLint errors)*

### Database Connection Error

- Check DATABASE_URL in .env
- Ensure PostgreSQL is running: `systemctl status postgresql`
- Verify database exists: `psql -U admin -l`

## Support

For issues and questions:
- Check [CHANGES.md](CHANGES.md) for recent updates
- View [DEVELOPMENT.md](DEVELOPMENT.md) for development guide
- Check API docs at http://localhost:8000/docs
- Review browser console (frontend) and terminal logs (backend)
# Health Assistant CI/CD Pipeline Setup Guide

This document describes the structure of the Health Assistant CI/CD pipeline, the required Gitea Secrets, and important configuration caveats for running in a self-hosted homelab environment.

---

## 1. Pipeline Structure Overview

The pipeline (`.gitea/workflows/deploy.yml`) is split into four highly optimized stages:

```
  ┌─────────────────┐       ┌──────────────────┐
  │  test-backend   │       │  test-frontend   │
  └────────┬────────┘       └────────┬─────────┘
           │                         │
           └────────────┬────────────┘
                        ▼
            ┌───────────────────────┐
            │    build-and-push     │
            └───────────┬───────────┘
                        ▼
            ┌───────────────────────┐
            │        deploy         │
            └───────────────────────┘
```

1. **`test-backend` (Concurrently):**
   - Launches Postgres (TimescaleDB) and Redis service containers.
   - Installs Python dependencies and runs the Pytest test suite.
2. **`test-frontend` (Concurrently):**
   - Installs Node.js dependencies.
   - Runs ESLint quality checks and performs a Vite production build check.
3. **`build-and-push`:**
   - Prepares all-lowercase image tags dynamically using the Gitea repository owner namespace (safely preventing lowercase reference format errors).
   - Authenticates and builds both backend and frontend images using the runner host's native `docker` daemon (bypassing isolated Buildx/BuildKit TLS certificate issues).
4. **`deploy`:**
   - Establishes an SSH connection to your target deployment server.
   - Transfers `docker-compose.prod.yml` and updates server-level `.env` configs.
   - Pulls updated images, restarts containers, and triggers automatic backend Alembic database migrations.
   - Verifies container health (checks for premature container exits after startup to fail the pipeline if a crash loop occurs).

---

## 2. Required Gitea Repository Secrets

Go to **Settings -> Actions -> Secrets** in your Gitea repository and configure the following:

| Secret Name | Expected Value Format / Description | Example |
| :--- | :--- | :--- |
| **`REGISTRY_HOST`** | The domain name of your registry **without** protocols or trailing slashes. | `gitea.example.com` |
| **`REGISTRY_TOKEN`** | A Gitea Personal Access Token (PAT) with `write:packages` and `read:packages` permissions. | `gtop_xxxxxxxxxxxxxxxxxxxx` |
| **`VM_HOST`** | IP address or domain of your target deployment server host. | `<DEPLOY_SERVER_IP>` |
| **`SSH_PRIVATE_KEY`** | The raw SSH private key authorized to connect as user `deploy` on the target server. | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| **`SECRET_KEY`** | Secret key for JWT hashing and session security. | *Any secure random string* |
| **`POSTGRES_PASSWORD`** | Production database password. | *Secure password* |
| **`OPENAI_API_KEY`** *(Optional)* | OpenAI API key for intelligent OCR and AI diagnostic features. | `sk-proj-...` |

---

## 3. Important Self-Hosted Runner Configurations (`config.yaml`)

To ensure Gitea Actions can run the pipeline smoothly, apply these critical configurations to your runner's `config.yaml` inside your runner's volume:

### A. Run Jobs Concurrently
Ensure your runner can execute parallel tasks:
```yaml
runner:
  capacity: 2  # Allows test-backend and test-frontend to run at the same time
```

### B. Network & Sibling Container DNS Resolution
To let spawned action containers resolve your local domain names without timeout errors, configure Gitea's docker network and add the host-gateway:
```yaml
container:
  network: "gitea-runner_default"  # Use the Docker network of your runner container
  options: "--add-host=gitea.example.com:host-gateway" # Replace with your Gitea domain
```

### C. Enable Actions Caching (Speeds up Node/Python setup from 7m to 15s)
```yaml
cache:
  enabled: true
  host: "runner"  # Matches the service name in your runner's docker-compose.yml
```

---

## 4. Reverse Proxy (Nginx) Requirements for Large Image Pushes

If you are using Gitea Container Registry behind Nginx, you must disable request buffering and increase timeouts, or large Docker layers (like Python and Node environments) will fail to push.

Add these directives inside your Gitea Nginx server configuration's `location /` block (replace `gitea.example.com` in your server block):

```nginx
location / {
    client_max_body_size 512M;

    # Disable buffering to stream layers directly to Gitea in real-time
    proxy_request_buffering off;
    proxy_buffering off;

    # Prevent premature connection dropouts on large layers
    proxy_read_timeout 600s;
    proxy_connect_timeout 600s;
    proxy_send_timeout 600s;
}
```

Reload Nginx after saving:
```bash
docker compose exec nginx nginx -s reload
```

---

## 5. Single-Domain Production Routing & Port Configuration (CORS & Proxying)

To completely eliminate Cross-Origin Resource Sharing (CORS) errors, the Health Assistant frontend and backend are configured to share the **exact same origin/domain** (e.g. `https://health_assistant.example.com`) in production:

- **Relative Paths:** Fallback API/GraphQL configurations in the code use relative paths `/api/v1` and `/graphql` instead of hardcoded localhosts.
- **Port Exposure:** Ports `3000` (frontend), `8000` (backend), and `5555` (flower) are exposed from the host server so that your centralized reverse proxy can securely route requests to them across your local network.
- **Vite Host Authorization:** Inside `vite.config.ts`, `preview.allowedHosts` is configured to `true` to allow Nginx proxy forwarding dynamically without exposing any private domain or IP details in your public repository.

---

## 6. Initial Database Seeding (First-Time Deploy Setup)

*Note: The detailed manual data seeding and user creation commands for production deployments have been moved to the [Installation Guide](./INSTALL.md#initial-database-seeding-for-production).*

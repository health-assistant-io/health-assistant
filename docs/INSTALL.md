# Health Assistant - Installation Guide

## Installations - Quickstart (Recommended)

Using Docker is the easiest and most recommended way to get Health Assistant up and running.

### Prerequisites
- Docker and Docker Compose
- 4GB RAM
- 10GB disk space

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
   Run the setup script to copy the template to `.env`, **automatically generate secure passwords and cryptographic keys**, and interactively configure your environment settings (URLs, workers, debug mode):
   ```bash
   python scripts/setup_env.py
   ```

   **Option B: Manual setup**
   If you cannot run the Python script, copy the template manually:
   ```bash
   cp .env.example .env
   ```
   *Note: If you choose manual setup, you **must** generate your own `SECRET_KEY`, `POSTGRES_PASSWORD`, `FLOWER_PASSWORD`, and `INTEGRATION_SECRET_KEY` (a base64url-encoded 32-byte Fernet key) and manually paste them into the `.env` file.*

4. **Configure remaining settings:**
   Open the newly created `.env` file in your preferred text editor. While your secure keys are set (if you used Option A), you should review and adjust other configurations like ports, `APP_URL`, or optional settings (email, AI, etc.) according to your environment.

5. **Start the application:**
   For development (hot-reloading):
   ```bash
   docker compose --env-file .env -f docker/docker-compose.yml up -d
   ```
   For production:
   ```bash
   docker compose --env-file .env -f docker/docker-compose.prod.yml up -d
   ```

6. **First-Time Data Seeding (Required for Production only):**
   *Note: If you started in development mode, this step is handled automatically. Skip to step 7.*
   
   If you started in **production mode** (`DEBUG=false`), you must manually seed the database and create your admin account:
   ```bash
   docker compose --env-file .env -f docker/docker-compose.prod.yml exec backend python scripts/create_system_admin.py --email admin@example.com --password securepassword --tenant "My Organization"
   ```

   ```bash
   docker compose --env-file .env -f docker/docker-compose.prod.yml exec backend python scripts/seed_biomarkers.py
   ```
   
   ```bash
   docker compose --env-file .env -f docker/docker-compose.prod.yml exec backend python scripts/seed_allergies.py
   ```
   
   ```bash
   docker compose --env-file .env -f docker/docker-compose.prod.yml exec backend python scripts/seed_medications.py
   ```

7. **Access the application:**
   Once the services are running, open your web browser and navigate to:
   - **Main Application (Frontend):** [http://localhost:3000](http://localhost:3000) - *This is the main user interface where you will interact with the Health Assistant.*
   - **API Documentation (Backend):** [http://localhost:8000/docs](http://localhost:8000/docs) - *Interactive developer documentation for the backend API.*

---

## Manual Installation

### Prerequisites for Manual Setup
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+ **with TimescaleDB extension** (e.g., `timescale/timescaledb:latest-pg14` Docker image)
  - *Note: TimescaleDB is recommended for telemetry hypertable + continuous aggregates. The migration now guards all TimescaleDB DDL behind an extension-availability check, so a plain PostgreSQL install will migrate successfully (it just skips hypertable creation). Install TimescaleDB if you need the telemetry/analytics features.*
- Redis 7+
- Tesseract OCR

### Step 1: Backend Setup

```bash
cd backend
```

Create virtual environment:
```bash
python -m venv venv
```

Activate virtual environment:
```bash
source venv/bin/activate  # Windows: venv\Scripts\activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Create environment file:
```bash
cp .env.example .env
```

Edit `.env` with your configuration (See Configuration section below).

Start backend:
```bash
uvicorn app.main:app --reload
```

Backend runs on: http://localhost:8000

### Step 2: Frontend Setup

```bash
cd frontend
```

Install dependencies:
```bash
npm install
```

Create environment file from the example:
```bash
cp .env.example .env
```

Start development server:
```bash
npm run dev
```

Frontend runs on: http://localhost:3000

## First-Time Setup & Linking

After your application is running and your data is seeded, you need to finalize your user setup.

### Development Environments
If you started the application in development mode:
1. **Register the First User**: Open the application at [http://localhost:3000](http://localhost:3000) and register. The very first user created on a fresh installation is automatically granted the **SYSTEM_ADMIN** role.
2. **Auto-Provisioning**: For home users, registering will automatically create a new **Household Tenant** and a **Default Organization**.

### Production Environments
If you started in production mode, you should have already run the `create_system_admin.py` command during the Quickstart.

1. **Log In**: Open the application at [http://localhost:3000](http://localhost:3000) (or your domain) and log in with the credentials you provided to the script.

### Final Step (All Environments)
3. **Link Your Profile**: Visit your profile settings in the app to link your User account to a Patient or Doctor record.

For more details on managing multiple users and clinical hierarchies, see the [Tenancy and User Management Guide](./TENANCY_AND_USER_MANAGEMENT.md).

## Configuration

Both the frontend and backend utilize `.env` files to document required configuration variables. 

1. Ensure you have run the setup script (or manually copied the example file) to create your active `.env` file:
   ```bash
   python scripts/setup_env.py
   ```
2. Open this file in your preferred text editor.
3. Review the inline comments within the `.env` file and supply any additional specific configuration. If you used the setup script, your secure keys have already been populated. If you chose manual setup, you must ensure all secure keys are generated and filled in.



## Verification

### Test Backend

```bash
curl http://localhost:8000/health
# Expected: {"status":"healthy","database":"connected","redis":"connected"}

curl http://localhost:8000/
# Expected: {"name":"Health Assistant","version":"0.3.0-rc.1","docs":"/docs"}
```

### Test Frontend

Open http://localhost:3000 in your browser

### Test Authentication

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=test123"
```

## Production Deployment

### Security Checklist

- [ ] Change `SECRET_KEY` to a secure random value *(handled by `setup_env.py` if used)*
- [ ] **Set `INTEGRATION_SECRET_KEY`** (Fernet key) *(handled by `setup_env.py` if used)*
- [ ] **Set `POSTGRES_PASSWORD`** to a strong, unique value *(handled by `setup_env.py` if used)*
- [ ] **Set `FLOWER_USER` and `FLOWER_PASSWORD`** *(handled by `setup_env.py` if used)*
- [ ] **Run the api_key backfill** if upgrading from a pre-0.3.0 release: `cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py`
- [ ] Set `DEBUG=false`
- [ ] Set `APP_ENV=production`
- [ ] Set `BACKEND_BIND=127.0.0.1` (default) — backend should NOT be directly internet-facing; place it behind nginx/Traefik for TLS termination.
- [ ] Use HTTPS/TLS (terminate at the reverse proxy)
- [ ] Configure firewall rules
- [ ] Set up database backups
- [ ] Configure rate limiting
- [ ] Enable logging and monitoring (Flower at `/flower` behind the reverse proxy is a good dashboard)
- [ ] **Set webhook secrets** for any integrations that receive webhooks — add `webhook_secret` to each integration's `user_config`; the sender must sign payloads with `HMAC-SHA256`

### Production Environment Modifications

When deploying to production, modify the variables within your `.env` file to ensure the system is secure:

- Update `APP_ENV` to `production`
- Update `DEBUG` to `false`
- Update `DATABASE_URL` and `REDIS_URL` to point to your production instances rather than `localhost` (if using external databases).

### Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name health_assistant.example.com;
    
    # Frontend
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    
    # Backend API
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

### HTTPS with Let's Encrypt

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
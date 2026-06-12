# Health Assistant - Installation Guide

## Last Updated: March 2026

## Prerequisites

### Minimum Requirements
- Python 3.11+
- Node.js 18+
- 4GB RAM
- 10GB disk space

### Optional (for full features)
- PostgreSQL 14+ with TimescaleDB
- Redis 7+
- Tesseract OCR

## Manual Installation

### Step 1: Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create environment file
cp .env.example .env

# Edit .env with your configuration
# See Configuration section below

# Start backend
uvicorn app.main:app --reload
```

Backend runs on: http://localhost:8000

#### Step 2: Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Create environment file from the example
cp .env.example .env

# Start development server
npm run dev
```

Frontend runs on: http://localhost:3000

### Method 3: Docker (Coming Soon)

Docker support is planned for future release.

## First-Time Setup

You can set up your initial administrator account using either the web interface or the command line.

### Method 1: Web Registration
1. **Register the First User**: Open the application and create an account. The very first user registered on a new installation is automatically granted the **SYSTEM_ADMIN** role.
2. **Auto-Provisioning**: For home users, registering without a `tenant_id` will automatically create a new **Household Tenant** and a **Default Organization** for you.

### Method 2: Command Line (Recommended for Production)
For more control, use the provided administration script:
```bash
cd backend
python scripts/create_system_admin.py --email admin@example.com --password securepassword --tenant "My Organization"
```

3. **Link Your Profile**: Visit your profile settings to link your User account to a Patient or Doctor record.

For more details on managing multiple users and clinical hierarchies, see the [Tenancy and User Management Guide](./TENANCY_AND_USER_MANAGEMENT.md).

## Configuration

Both the frontend and backend utilize `.env.example` files to document required configuration variables. 

1. Ensure you have copied the example files to create your active `.env` files:
   - Backend: `cp backend/.env.example backend/.env`
   - Frontend: `cp frontend/.env.example frontend/.env`
2. Open these files in your preferred text editor.
3. Review the inline comments within the `.env` files and supply your required credentials (e.g., `OPENAI_API_KEY`, database credentials).

## Verification

### Test Backend

```bash
curl http://localhost:8000/health
# Expected: {"status":"healthy","database":"connected","redis":"connected"}

curl http://localhost:8000/
# Expected: {"name":"Health Assistant","version":"0.1.2","docs":"/docs"}
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

- [ ] Change `SECRET_KEY` to a secure random value
- [ ] Set `DEBUG=false`
- [ ] Set `APP_ENV=production`
- [ ] Use HTTPS/TLS
- [ ] Configure firewall rules
- [ ] Set up database backups
- [ ] Configure rate limiting
- [ ] Enable logging and monitoring

### Generate Secure SECRET_KEY

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Production Environment Modifications

When deploying to production, modify the variables within your `backend/.env` file to ensure the system is secure:

- Update `APP_ENV` to `production`
- Update `DEBUG` to `false`
- Update `SECRET_KEY` with the securely generated token from the step above.
- Update `DATABASE_URL` and `REDIS_URL` to point to your production instances rather than `localhost`.

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
sudo certbot --nginx -d health_assistant.example.com
```

## Updates

### Manual Update

```bash
# Backend
cd backend
git pull
source venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend
git pull
npm install
```

## Troubleshooting

### Port Already in Use

```bash
# Find and kill process on port 8000
lsof -i :8000
lsof -ti:8000 | xargs kill -9

# Find and kill process on port 3000
lsof -i :3000
lsof -ti:3000 | xargs kill -9
```

### Backend Import Errors

```bash
cd backend
source venv/bin/activate
python -c "from app.main import app"
# Should output no errors
```

### Frontend Build Errors

```bash
cd frontend
npm run build
# Check for TypeScript/ESLint errors
```

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
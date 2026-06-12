# Health Assistant Backend

For the full project directory structure, please refer to [docs/PROJECT_STRUCTURE.md](../docs/PROJECT_STRUCTURE.md).

## Core Components

### Configuration (`app/core/config.py`)
Uses **Pydantic BaseSettings** to manage all environment variables, database connections, and external API keys. It handles the assembly of PostgreSQL and Redis connection strings and defines fallback configurations for AI providers.

### Database Models (`app/models/`)
The persistence layer is built on **SQLAlchemy 2.0**. 
- **Base Mixins (`base.py`)**: Provide standardized UUID primary keys, multi-tenant isolation, auditing (creator/updater tracking), and timestamps.
- **FHIR Resources (`fhir/`)**: Implement healthcare-standard representations for Patients, Observations, and Medications.
- **Multi-Tenancy**: Every clinical record is strictly isolated by a `tenant_id` to support household-level or clinic-level data segregation.

### AI Processing Pipeline
Health Assistant features a modular, provider-agnostic AI architecture:
- **OCR**: Handles vision-to-text conversion (supporting OpenAI Vision and Tesseract).
- **NLP**: Uses LangChain and specialized clinical models to extract structured FHIR data from raw medical text.
- **Service Integration**: The `AIProviderService` manages dynamic model configuration per tenant and task type.

For detailed documentation on the AI system, see the [AI System Architecture](../docs/AI_SYSTEM.md) guide.

### Clinical Ontology & Biomarker Management
A robust, metadata-driven system for managing medical definitions:
- **Definitions**: Reference ranges and clinical context are stored in the database, not hardcoded.
- **Importing**: Supports bulk-importing standardized catalogs (LOINC/SNOMED) via the Admin UI.
- **AI Fallback**: The NLP pipeline can autonomously predict definitions for unknown biomarkers and register them to the catalog.

### Domain Services (`app/services/`)
- **Unit Converter**: Handles clinical unit normalization (e.g., mg/dL to mmol/L).
- **Anomaly Detector**: Identifies statistical trends and reference range violations.
- **Medication Interactor**: Checks for potential drug-drug interactions via RxNorm integration.
- **Notification Service**: Manages multi-channel alerts (In-App, Push, Email).

### Asynchronous Workers (`app/workers/`)
Uses **Celery + Redis** for long-running tasks like OCR processing, large-scale data imports, and scheduled clinical alerts.

## Requirements

All backend dependencies are actively managed in `requirements.txt`. Please refer to that file for the complete, up-to-date list of required packages.

## Getting Started

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your configuration

# Run migrations
alembic upgrade head

# Create a system administrator
python scripts/create_system_admin.py --email admin@example.local --password securepassword

# Start development server
uvicorn app.main:app --reload
```

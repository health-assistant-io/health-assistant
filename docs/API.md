# Health Assistant - API Documentation

## Overview

Health Assistant provides RESTful APIs for interacting with the health data platform.

**Base URL**: `http://localhost:8000/api/v1`  
**API Docs**: `http://localhost:8000/docs`

## Authentication

All API endpoints (except `/auth/login` and `/auth/register`) require authentication.

### JWT Token

Include your JWT token in the `Authorization` header:

```
Authorization: Bearer <your-jwt-token>
```

### Login Endpoint

```http
POST /api/v1/auth/login
```

**Request Body:**
```
username=test@example.com
password=your-password
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Refresh Token

```http
POST /api/v1/auth/refresh
```

**Request Body:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Register New User

```http
POST /api/v1/auth/register
```

**Request Body:**
- `email`: (Required) User email address
- `password`: (Required) Password (min 8 characters)
- `tenant_id`: (Optional) Tenant ID to join.

**Note on Auto-Provisioning:**
- If `tenant_id` is omitted, the system automatically creates a new **Tenant** and a **Default Household** organization for the user.
- The first user registered on a new installation is automatically promoted to **SYSTEM_ADMIN**.
- Subsequent users registering without a `tenant_id` are promoted to **ADMIN** of their own new household.

**Response:** `200 OK`
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "role": "SYSTEM_ADMIN",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001"
}
```

### Validate Token

```http
GET /api/v1/auth/validate
```

**Response:** `200 OK`
```json
{
  "valid": true,
  "user_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

### Get Current User

```http
GET /api/v1/users/me
```

**Response:** `200 OK`
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "role": "user",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001"
}
```

## REST API

### Tenants

#### Create Tenant

```http
POST /api/v1/tenants
```

**Request Body:**
```json
{
  "name": "Acme Corporation",
  "settings": {
    "data_retention_days": 2555,
    "unit_system": "metric"
  }
}
```

**Response:** `201 Created`

#### Get Tenant

```http
GET /api/v1/tenants/{tenant_id}
```

**Response:** `200 OK`

#### Update Tenant

```http
PUT /api/v1/tenants/{tenant_id}
```

**Request Body:** (same as create)

#### Delete Tenant

```http
DELETE /api/v1/tenants/{tenant_id}
```

**Note:** Soft delete - sets `deleted_at` timestamp

### Users

#### Create User

```http
POST /api/v1/users
```

**Request Body:**
```json
{
  "email": "john.doe@example.com",
  "password": "SecurePass123!",
  "role": "user",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001"
}
```

**Response:** `201 Created`

#### Get Current User

```http
GET /api/v1/users/me
```

**Response:** `200 OK`

#### Update User

```http
PUT /api/v1/users/{user_id}
```

#### Delete User
```http
DELETE /api/v1/users/{user_id}
```

### User Administration

#### List Tenant Users
```http
GET /api/v1/users
```
Returns all users belonging to the current tenant. Admin/Manager roles only.

#### Create User
```http
POST /api/v1/users
```
**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "temporarypassword123",
  "role": "USER",
  "tenant_id": "optional-uuid"
}
```
If `tenant_id` is omitted, the user is created in the administrator's current tenant.

### Documents

#### Upload Document

```http
POST /api/v1/documents
```

**Form Data:**
- `file`: PDF, JPEG, PNG, DOCX, or TXT file
- `patient_id`: Optional patient reference

**Response:** `202 Accepted`

#### Get Document

```http
GET /api/v1/documents/{document_id}
```

#### Download Document

```http
GET /api/v1/documents/{document_id}/download
```

**Response:** File download

#### Trigger Extraction

```http
POST /api/v1/documents/{document_id}/extract
```

**Response:** `202 Accepted`

#### Get Extraction Status

```http
GET /api/v1/documents/{document_id}/extract/status
```

**Response:**
```json
{
  "status": "completed",
  "progress": 100,
  "extracted_at": "2024-01-15T11:05:00Z"
}
```

### FHIR Resources

#### Create Patient

```http
POST /api/v1/fhir/Patient
```

**Request Body:** (FHIR Patient resource)

#### List Patients

```http
GET /api/v1/fhir/Patient
```

**Query Parameters:**
- `_count`: Number of results per page
- `_page`: Page number
- `name`: Filter by name

#### Get Patient

```http
GET /api/v1/fhir/Patient/{id}
```

#### Create Observation

```http
POST /api/v1/fhir/Observation
```

**Request Body:** (FHIR Observation resource)

#### List Observations

```http
GET /api/v1/fhir/Observation
```

**Query Parameters:**
- `patient`: Filter by patient ID
- `code`: Filter by LOINC code
- `date`: Filter by date range
- `_sort`: Sort field

#### Get Observation History

```http
GET /api/v1/fhir/Observation/history
```

**Query Parameters:**
- `patient`: Patient ID
- `code`: LOINC code
- `period`: Time period

#### Create DiagnosticReport

```http
POST /api/v1/fhir/DiagnosticReport
```

#### Get DiagnosticReport

```http
GET /api/v1/fhir/DiagnosticReport/{id}
```

#### Create Medication

```http
POST /api/v1/fhir/Medication
```

#### Get Medication

```http
GET /api/v1/fhir/Medication/{id}
```

#### List Medications
- `POST /api/v1/fhir/Medication` - Create medication

### Clinical Events

#### List Event Types
```http
GET /api/v1/clinical-events/types
```
Returns all available event categories (Pregnancy, Pain, Dental, etc.).

#### List Events
```http
GET /api/v1/clinical-events
```
**Query Parameters:**
- `patient_id`: Filter by patient
- `examination_id`: Filter by linked examination
- `status`: Filter by `active`, `resolved`, etc.

#### Create Event
```http
POST /api/v1/clinical-events
```
**Request Body:**
```json
{
  "patient_id": "uuid",
  "type_id": "uuid",
  "title": "Third Pregnancy",
  "description": "Routine monitoring",
  "onset_date": "2026-01-15T10:00:00Z",
  "event_metadata": {
    "lmp": "2026-01-01"
  },
  "examinations": [
    { "examination_id": "uuid", "reason": "Initial scan" }
  ]
}
```

#### Link Examination
```http
POST /api/v1/clinical-events/{event_id}/link-examination
```
Associate an existing visit with an event.

#### List Correlated Biomarkers
```http
GET /api/v1/clinical-events/types/{type_id}/biomarkers
```
Returns all biomarkers conceptually linked to a specific event type (e.g., Vision -> Visual Acuity).

### Wearable Data


#### Upload Wearable Data

```http
POST /api/v1/wearable/data
```

**Request Body:**
```json
{
  "device_id": "garmin-vivoactive-4",
  "data": [
    {
      "timestamp": "2024-01-15T08:00:00Z",
      "heart_rate": 72,
      "steps": 1500,
      "calories": 250
    }
  ]
}
```

**Response:** `201 Created`

#### Get Wearable Data

```http
GET /api/v1/wearable/data
```

**Query Parameters:**
- `device_id`: Filter by device
- `start_date`: Start timestamp
- `end_date`: End timestamp
- `metrics`: Comma-separated metrics

#### Get Daily Summary

```http
GET /api/v1/wearable/data/summary
```

**Query Parameters:**
- `date`: Date
- `device_id`: Filter by device

### Alerts & Notifications

#### Create Alert

```http
POST /api/v1/alerts
```

**Request Body:**
```json
{
  "type": "high_glucose",
  "patient_id": "123e4567-e89b-12d3-a456-426614174002",
  "threshold": 7.0,
  "enabled": true
}
```

#### List Alerts

```http
GET /api/v1/alerts
```

#### Get Alert History

```http
GET /api/v1/alerts/history
```

### Notifications & Push

#### Get VAPID Public Key

```http
GET /api/v1/notifications/vapid-public-key
```

Returns the public key required for PWA Web Push registration.

#### Register Subscription

```http
POST /api/v1/notifications/subscribe
```

**Request Body:**
```json
{
  "subscription": {
    "endpoint": "...",
    "keys": { "p256dh": "...", "auth": "..." }
  },
  "device_id": "optional-uuid",
  "user_agent": "..."
}
```

#### List Notifications

```http
GET /api/v1/notifications
```

**Query Parameters:**
- `patient_id`: (Required) Filter by patient
- `unread_only`: (Boolean)
- `limit`: Default 20

#### Mark as Read

```http
PATCH /api/v1/notifications/{id}/read
```

#### Create Trigger

```http
POST /api/v1/notifications/triggers
```

**Request Body:**
```json
{
  "patient_id": "uuid",
  "title": "Reminder",
  "body": "Message",
  "notification_type": "medication_reminder",
  "trigger_type": "recurring",
  "config": {
    "at": "09:00",
    "days": ["mon", "wed"]
  }
}
```

### Analytics

#### Get Dashboard Data

```http
GET /api/v1/analytics/dashboard
```

**Query Parameters:**
- `patient_id`: Filter by patient
- `period`: Time period

#### Get Biomarker Trends

```http
GET /api/v1/analytics/trends
```

**Query Parameters:**
- `biomarker_codes`: Comma-separated slugs, LOINC codes, or display names. When provided, the filter is expanded against the definition catalog (names, aliases, codes) so both mapped and unmapped observations are included.
- `period`: Time period (e.g., 'last-30-days', 'last-12-months', 'all-time')
- `aggregation`: Optional resolution bucket for telemetry data (e.g., '15 minutes', '1 day', '1 week')
- `patient_id`: Optional override for admin context

#### Remap Observations to Definition

```http
POST /api/v1/biomarkers/{biomarker_id}/remap
```

Relinks unmapped observations (those with `biomarker_id = NULL`) to a biomarker definition. Matches observations whose stored `code.text` equals `source_name` (case-insensitive). Used by the frontend's "Create biomarker" / "Map to existing" popup on unmapped biomarkers.

**Request Body:**
```json
{
  "source_name": "WBC",
  "patient_id": "uuid-of-patient (optional)"
}
```

**Response:**
```json
{
  "status": "success",
  "biomarker_id": "3e9e3f7e-42f1-439f-a73c-64fd6234aed5",
  "observations_remapped": 3
}
```

#### Get Analytics Summary

```http
GET /api/v1/analytics/summary
```

**Query Parameters:**
- `patient_id`: Filter by patient
- `period`: Time period

#### Get Reference Ranges

```http
GET /api/v1/analytics/reference-ranges
```

## Error Handling

### Standard Error Response

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input data",
    "details": [
      {
        "field": "email",
        "message": "Invalid email format"
      }
    ]
  }
}
```

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 202 | Accepted (async operation) |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 500 | Internal Server Error |

## Current API Status

### ✅ Working Endpoints

#### Authentication
- `POST /api/v1/auth/login` - Login and get tokens
- `POST /api/v1/auth/refresh` - Refresh access token
- `GET /api/v1/auth/validate` - Validate current token
- `POST /api/v1/auth/register` - Register new user
- `GET /api/v1/users/me` - Get current user

#### Users
- `GET /api/v1/users/{id}` - Get user by ID
- `PUT /api/v1/users/{id}` - Update user
- `DELETE /api/v1/users/{id}` - Delete user

#### Tenants
- `GET /api/v1/tenants/{id}` - Get tenant
- `POST /api/v1/tenants` - Create tenant
- `PUT /api/v1/tenants/{id}` - Update tenant
- `DELETE /api/v1/tenants/{id}` - Delete tenant

#### Documents
- `POST /api/v1/documents` - Upload document
- `GET /api/v1/documents/{id}` - Get document
- `GET /api/v1/documents/{id}/download` - Download document
- `POST /api/v1/documents/{id}/extract` - Trigger extraction
- `GET /api/v1/documents/{id}/extract/status` - Get extraction status

#### FHIR Resources
- `GET /api/v1/fhir/Patient` - List patients
- `POST /api/v1/fhir/Patient` - Create patient
- `GET /api/v1/fhir/Patient/{id}` - Get patient
- `GET /api/v1/fhir/Observation` - List observations
- `POST /api/v1/fhir/Observation` - Create observation
- `GET /api/v1/fhir/Observation/history` - Get observation history
- `GET /api/v1/fhir/DiagnosticReport/{id}` - Get diagnostic report
- `POST /api/v1/fhir/DiagnosticReport` - Create diagnostic report
- `GET /api/v1/fhir/Medication` - List medications
- `POST /api/v1/fhir/Medication` - Create medication

#### Wearable Data
- `POST /api/v1/wearable/data` - Upload wearable data
- `GET /api/v1/wearable/data` - Get wearable data
- `GET /api/v1/wearable/data/summary` - Get daily summary
- `GET /api/v1/wearable/anomalies` - Detect anomalies

#### Alerts
- `GET /api/v1/alerts` - List alerts
- `POST /api/v1/alerts` - Create alert
- `GET /api/v1/alerts/{id}` - Get alert
- `PUT /api/v1/alerts/{id}` - Update alert
- `DELETE /api/v1/alerts/{id}` - Delete alert
- `POST /api/v1/alerts/{id}/trigger` - Trigger alert
- `GET /api/v1/alerts/history` - Get alert history

#### Analytics
- `GET /api/v1/analytics/dashboard` - Get dashboard data
- `GET /api/v1/analytics/trends` - Get biomarker trends
- `GET /api/v1/analytics/summary` - Get analytics summary
- `GET /api/v1/analytics/reference-ranges` - Get reference ranges

#### Notifications
- `GET /api/v1/notifications/vapid-public-key` - Get VAPID key
- `POST /api/v1/notifications/subscribe` - Register for push
- `GET /api/v1/notifications` - List patient notifications
- `PATCH /api/v1/notifications/{id}/read` - Mark read
- `POST /api/v1/notifications/triggers` - Create manual trigger

#### Export & Import (Backup)

See [EXPORT_IMPORT.md](EXPORT_IMPORT.md) for the full format/scopes/restore spec.

- `POST /api/v1/export` - Create an export job (body: `BackupRequest` with `scope` ∈ {patient, group, system}, `export_type` ∈ {fhir_only, full_backup, catalog_only}, optional `patient_ids`). Enqueues a Celery task; returns `ExportJobResponse`.
- `GET /api/v1/export/jobs` - List export jobs for the current tenant.
- `GET /api/v1/export/jobs/{job_id}` - Get export job status.
- `GET /api/v1/export/jobs/{job_id}/download` - Download the generated file (FHIR Bundle `.fhir.json`, full-backup `.zip`, or catalog `.catalog.json`).
- `POST /api/v1/import/backup` - Upload a ZIP or bare `bundle.json`/`catalog.json` (multipart `file`). Enqueues a Celery task; returns `ImportJobResponse`.
- `GET /api/v1/import/jobs/{job_id}` - Get backup-import job status + `restore_result` (created/updated counts, `manifest_verified`, `fhir_validated`).
- `POST /api/v1/import/fhir` - Synchronous FHIR Bundle import (smaller bundles).
- `POST /api/v1/import/csv` - CSV import (legacy).
- `POST /api/v1/import/ocr` - OCR extraction (legacy).

**Role gating**: `patient` scope — any role (`USER` limited to a single own patient); `group` — `MANAGER`+; `system` — `ADMIN`+. `SYSTEM_ADMIN` bypasses all checks.

### ⚠️ Legacy Note

Some internal service functions might still use fallback logic if specific external dependencies (like local OCR engines or AI providers) are not fully configured in the `.env` file. However, the core database CRUD and multi-tenancy logic are fully implemented.

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
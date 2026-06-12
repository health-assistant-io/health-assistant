# User Health Data Integrations Architecture

## Overview
This document outlines the architecture for allowing end-users to securely connect third-party health platforms (e.g., Google Fit, Apple Health, Epic MyChart) to their Health Assistant profile. 

The system relies on an **Integration Hub** architecture. Data from third parties is fetched via background tasks, normalized into standard FHIR `Observation` records using our Clinical Ontology, and saved seamlessly into the unified patient record.

## 1. Database Schema Additions

A new table `user_integrations` will be created to securely store OAuth credentials and sync states.

```python
class UserIntegration(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    __tablename__ = "user_integrations"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("fhir_patients.id", ondelete="CASCADE"), nullable=False)
    
    provider = Column(String(50), nullable=False) # e.g., 'google_fit', 'apple_health'
    status = Column(Enum(IntegrationStatus), default=IntegrationStatus.PENDING)
    
    # OAuth Credentials (Should be encrypted in a production environment)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(String(500), nullable=True)
    
    provider_account_id = Column(String(255), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uix_user_provider"),)
```

## 2. Pluggable Provider Interface

To ensure the architecture is highly modular and scalable for future platforms, all integrations must implement a base interface.

```python
class BaseHealthProvider(ABC):
    provider_id: str
    
    @abstractmethod
    async def get_auth_url(self, state: str) -> str:
        """Return the OAuth redirect URL."""
        pass

    @abstractmethod
    async def exchange_token(self, code: str) -> Dict[str, Any]:
        """Exchange the auth code for access/refresh tokens."""
        pass
        
    @abstractmethod
    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Get a new access token using a refresh token."""
        pass
        
    @abstractmethod
    async def fetch_data(self, integration: UserIntegration, start_date: datetime, end_date: datetime) -> List[Observation]:
        """Fetch raw data and map it directly to our FHIR Observation format."""
        pass
```

## 3. Implementation Strategies

### Cloud-to-Cloud (OAuth via backend)
For cloud-synced services, the backend handles OAuth tokens and runs a background Celery task to fetch data directly from the provider's REST API.
*   **Fitbit Web API:** Google has migrated cloud health data access to Fitbit. We will use the Fitbit Web API for retrieving steps, heart rate, and sleep data from Google ecosystem users.
*   **SMART on FHIR (Epic/Cerner):** Allows patients to connect to their hospital's patient portal (like MyChart) to securely sync official clinical lab results and `DiagnosticReports` directly into our FHIR database.
*   **Oura / Withings:** Other common cloud-accessible health APIs.

### Device-to-Cloud (Mobile Client Push)
**Important Note on Google Fit:** The Google Fit REST API is officially deprecated and transitioning to **Health Connect** (for Android) and **Apple HealthKit** (for iOS). 
*   Because Health Connect and HealthKit are *on-device APIs*, they do not offer a cloud REST endpoint for our backend to query.
*   To integrate with these, a mobile companion app (or PWA with device permissions) must be built. The mobile app reads the data locally on the phone and **PUSHES** it to our backend using the existing `POST /api/v1/wearable/data` endpoint.
*   No OAuth backend flow is required for this approach.

## 4. API Endpoints (`app/api/v1/endpoints/integrations.py`)

*   `GET /api/v1/integrations` - List the current user's active integrations.
*   `GET /api/v1/integrations/{provider}/auth-url` - Returns the URL to redirect the user to for OAuth.
*   `GET /api/v1/integrations/{provider}/callback` - Handles the OAuth redirect, exchanges the code, and saves the `UserIntegration` record.
*   `POST /api/v1/integrations/{provider}/sync` - Triggers an immediate manual sync for a specific provider.
*   `DELETE /api/v1/integrations/{provider}` - Revokes access and deletes the integration.

## 5. Synchronization Engine

A Celery background task `sync_active_integrations` will be scheduled to run every 12 hours.
1. It queries `UserIntegration` where status is `ACTIVE`.
2. Checks if `expires_at` is near. If so, calls `provider.refresh_access_token()`.
3. Calls `provider.fetch_data(last_synced_at, now)`.
4. Saves new `Observation` records to the database.
5. Updates `last_synced_at`.

## 6. Frontend UI
*   Add a new page `Integrations.tsx` under the Settings navigation menu.
*   Display a catalog of "Available Providers" (Google Fit).
*   Handle the OAuth callback routing (`/integrations/callback`).
*   Update dashboard charts to indicate which `Observations` were sourced automatically from integrations (vs. manual/PDF upload).
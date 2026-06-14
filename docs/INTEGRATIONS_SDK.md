# Integrations SDK & Developer Guide

Health Assistant provides a powerful **Integrations SDK** (`app.integrations.sdk`) designed to make building new third-party integrations simple, secure, and robust.

This guide walks you through creating an integration, utilizing SDK tools, and handling both Polling (pull) and Webhook (push) data flows.

For an overview of the system architecture, see the [Integrations Framework](INTEGRATIONS_FRAMEWORK.md) document.

---

## 1. Directory Structure

To create a new integration, create a folder under `backend/app/integrations/{domain}`. The domain should be a lowercase string (e.g., `fitbit`, `notify`).

Your integration must include three core files:
1. `manifest.json`
2. `config_flow.py`
3. `provider.py`

### Step 1: `manifest.json`
Defines the metadata of your integration.

```json
{
  "domain": "notify",
  "name": "Notify for Xiaomi",
  "version": "1.0.0",
  "integration_type": ["push"], 
  "dependencies": []
}
```
*Note: `integration_type` can be `["pull"]` for polling APIs or `["push"]` for inbound webhooks.*

### Step 2: `config_flow.py`
Defines the dynamic UI needed to set up this integration. Must inherit from `BaseConfigFlow`.

```python
from app.integrations.sdk import BaseConfigFlow

class NotifyConfigFlow(BaseConfigFlow):
    domain = "notify"
    
    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Notify",
            "description": "Select the metrics you want to track.",
            "data_schema": {
                "type": "object",
                "properties": {
                    "track_heart_rate": {"type": "boolean", "default": True},
                    "track_steps": {"type": "boolean", "default": True}
                }
            }
        }
        
    async def validate_input(self, user_input: dict) -> dict:
        # Perform any validation. Raise ValueError if invalid.
        return user_input
```

### Step 3: `provider.py`
The core logic. Must inherit from `BaseHealthProvider`.

```python
from typing import List, Any
from app.integrations.sdk import BaseHealthProvider
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration

class NotifyProvider(BaseHealthProvider):
    domain = "notify"
    
    # ... See below for implementation details (Pull vs Push) ...
```

---

## 2. Handling Data (Pull vs. Push)

### Option A: Polling (Pull Data)
For REST APIs that require periodic polling (e.g., Fitbit), implement the `pull_data` method. A background Celery worker runs every 15 minutes to call this method.

```python
    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        last_sync = self.get_sync_cursor(integration, "last_date", default="2024-01-01")
        
        # Use the built-in HTTP client
        data = await self.fetch_json(
            integration, 
            f"https://api.example.com/data?since={last_sync}"
        )
        
        observations = []
        builder = self.create_observation_builder(integration)
        
        # ... parse data and build observations ...
        
        self.set_sync_cursor(integration, "last_date", "2024-02-01")
        return observations
```

### Option B: Webhooks (Push Data)
For platforms that push data to Health Assistant (e.g., Notify App, Tasker), implement the `handle_webhook` method.

The system automatically provisions a unique endpoint for each connected user:
`POST /api/v1/integrations/{domain}/webhook/{integration_id}`

```python
    async def handle_webhook(self, integration: UserIntegration, payload: Any, request: Any = None) -> List[ObservationCreate]:
        
        await self.log_debug_payload(integration, "Incoming Webhook", payload)
        
        observations = []
        builder = self.create_observation_builder(integration)
        config = integration.user_config or {}
        
        if payload.get("type") == "heart_rate" and config.get("track_heart_rate"):
            obs = (
                builder
                .set_biomarker("8867-4", "Heart rate")
                .set_value(float(payload["value"]), "bpm", "{beats}/min")
                .build()
            )
            observations.append(obs)

        return observations
```

---

## 3. Advanced SDK Features

### 3.1 Robust HTTP Client & Auto-Retries
`BaseHealthProvider` natively provides `self.fetch_json()` which automatically handles:
- **Connection pooling** to preserve sockets.
- **Exponential backoff** for 5xx server errors and network timeouts.
- **Rate Limit awareness**, explicitly pausing if a `429 Too Many Requests` is hit (respecting `Retry-After` headers).

### 3.2 Cursor / State Management (Delta Syncs)
Save your position (cursor) between syncs to avoid pulling duplicate data.
```python
# Retrieve last state
last_timestamp = self.get_sync_cursor(integration, "last_timestamp", default=0)

# Save new state
self.set_sync_cursor(integration, "last_timestamp", new_timestamp)
```

### 3.3 Managed Exceptions & UI Feedback
Throw specific exceptions from `app.integrations.sdk.exceptions` to instruct the core engine.
- `IntegrationAuthError`: Pauses the integration (changes status to `ERROR`) and alerts the user in the UI. Automatically raised by `fetch_json()` on 401/403 responses.
- `IntegrationRateLimitError`: Skips the current sync cycle gracefully. Automatically raised if 429 retries are exhausted.

### 3.4 Payload Debugging
When deciphering undocumented third-party APIs or receiving webhooks, you can securely dump raw payloads into a dedicated debug database. These logs can then be viewed directly in the Integration's Debug Console UI.

The platform automatically provides a **"Debug Mode: OFF/ON"** button for every connected integration in the frontend UI. You do not need to add any settings to your `config_flow.py`!

To utilize this in your provider code, simply call:

```python
# Automatically checks if integration.is_debug_enabled before saving
await self.log_debug_payload(integration, "API Response", raw_json_data)
```

### 3.5 Custom Actions (Services)
Integrations can expose custom interactive buttons to the frontend UI (e.g., "Reset Sync Cursor", "Trigger Device Ping").

```python
    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"}
        ]
        
    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_date", None)
            return {"message": "Cursor reset successfully!"} # Returns a Toast to the UI
            
        raise NotImplementedError(f"Action not supported.")
```

---

## 4. Building FHIR Observations
Use the `ObservationBuilder` to map raw third-party data into FHIR compliant schemas easily.

### Supported Coding Systems
When building observations, the system relies on coding systems to uniquely identify biomarkers without collisions. The `set_biomarker()` method defaults to LOINC, but you can explicitly define the system using the `CodingSystem` enum:

* **`CodingSystem.LOINC`** (Default) - Used for standard clinical laboratory measurements and standard vital signs.
* **`CodingSystem.SNOMED`** - Used for clinical terms and findings.
* **`CodingSystem.CUSTOM`** - **Crucial for wearables/IoT.** Use this when the external API provides a proprietary identifier (e.g., Apple's `HKQuantityTypeIdentifierStepCount` or Garmin's internal metric keys) to ensure it does not accidentally collide with a real clinical code.


> **Important Developer Note:** You might wonder how telemetry data (like high-frequency heart rate from a wearable) gets saved to the TimescaleDB hypertable if you are returning standard FHIR `ObservationCreate` objects. 
> 
> **You do not need to interact with TimescaleDB directly.** Simply return `ObservationCreate` objects for all data. The platform's core sync engine automatically intercepts your observations. It checks the associated Biomarker Definition's `is_telemetry` flag. If it is `true`, the sync engine will dynamically mutate the observation and route it to the `telemetry_data` hypertable instead of saving it as a standard FHIR observation.

```python
builder = self.create_observation_builder(integration)

obs = (
    builder
    .set_biomarker("8867-4", "Heart rate") # Defaults to LOINC Code
    .set_value(72.0, "bpm", "{beats}/min")
    .set_reference_range(low=60, high=100)
    .set_effective_date(timestamp_obj) # Defaults to now() if omitted
    .build()
)

# For proprietary codes (e.g., Apple HealthKit keys):
from app.models.enums import CodingSystem

obs_custom = (
    builder
    .set_biomarker("HKQuantityTypeIdentifierStepCount", "Step Count", coding_system=CodingSystem.CUSTOM)
    .set_value(5000, "steps", "{steps}")
    .build()
)
```

---

## 5. Enable your Integration
By default, newly written integrations are invisible. A system administrator must execute a command or use the Admin UI to enable it globally.

Via SQL:
```sql
INSERT INTO system_integrations (domain, is_enabled) 
VALUES ('notify', true)
ON CONFLICT (domain) DO UPDATE SET is_enabled = true;
```
# Integrations Framework

Health Assistant includes a robust, pluggable Integrations Framework inspired by Home Assistant. It allows users to securely connect third-party platforms (like wearables, labs, or notifications) to their personal profile.

## Architecture Highlights

1. **Pluggable & Modular**: Each integration lives in its own isolated folder under `backend/app/integrations/`.
2. **Two-Tiered Enablement**:
   - **System Admins** determine if an integration is available to the platform (saving global API credentials).
   - **Users** connect their individual accounts and control sync preferences.
3. **Dynamic Schema Setup (Config Flow)**: Instead of hardcoding forms in React, integrations expose a JSON Schema. The frontend dynamically generates the setup UI.
4. **Unified Sync Engine**: A background Celery worker automatically runs every 15 minutes, fetching data and mapping it to standard FHIR resources.

## Advanced SDK Features

The Integrations SDK provides powerful built-in tools to handle real-world API challenges safely and efficiently.

### 1. Robust HTTP Client & Auto-Retries
You do not need to bring your own `requests` or `httpx` client. `BaseHealthProvider` natively provides `self.fetch_json()` which automatically handles:
- **Connection pooling** to preserve sockets in background workers.
- **Exponential backoff** for 5xx server errors and network timeouts.
- **Rate Limit awareness**, explicitly pausing if a `429 Too Many Requests` is hit (respecting `Retry-After` headers).

### 2. Cursor / State Management (Delta Syncs)
To avoid pulling years of historical data every 15 minutes, save your position (cursor) between syncs.
```python
# Retrieve the last timestamp we synced (defaults to 0 on first run)
last_timestamp = self.get_sync_cursor(integration, "last_timestamp", default=0)

# ... fetch data newer than last_timestamp ...

# Save the new cursor. The engine will safely persist this to the DB!
self.set_sync_cursor(integration, "last_timestamp", new_latest_timestamp)
```

### 3. Managed Exceptions & UI Feedback
Throw specific exceptions from `app.integrations.sdk.exceptions` to instruct the core engine how to handle failures. These are surfaced directly in the frontend UI.
- `IntegrationAuthError`: Pauses the integration (changes status to `ERROR`) and alerts the user in the UI that they must re-authenticate or fix their settings.
- `IntegrationRateLimitError`: Skips the current sync cycle gracefully without marking the integration as broken.

*(Note: `fetch_json()` automatically raises `IntegrationAuthError` on 401/403 responses and `IntegrationRateLimitError` if 429 retries are exhausted!)*

### 4. Payload Debugging
When deciphering undocumented third-party APIs, you can dump raw payloads into the backend logs safely.
```python
# Only executes if the user has "debug_mode": true in their integration config
self.log_debug_payload(integration, "Fitbit Response", raw_json_data)
```

### 5. Custom Actions (Services)
Integrations can expose custom interactive buttons to the frontend UI (e.g., "Reset Sync Cursor", "Trigger Device Ping", "Clear Logs").

```python
    def get_custom_actions(self) -> List[Dict[str, str]]:
        # This automatically renders buttons in the UI
        return [
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"},
            {"id": "ping_device", "label": "Ping Device", "style": "primary"}
        ]
        
    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_date", None)
            return {"message": "Cursor reset successfully!"} # Returns a Toast notification to the UI
            
        raise NotImplementedError(f"Action '{action_id}' is not supported.")
```

---

## Creating a New Integration

To create a new integration, create a folder under `backend/app/integrations/{domain}`. It is recommended to use the **Integrations SDK** for a more streamlined development experience.

### 1. `manifest.json`
Defines the metadata of your integration.
```json
{
  "domain": "fitbit",
  "name": "Fitbit Connect",
  "version": "1.0.0",
  "integration_type": ["pull"],
  "dependencies": []
}
```

### 2. `config_flow.py`
Defines the UI needed to set up this integration. Must inherit from `BaseConfigFlow` (available in `app.integrations.sdk`).

```python
from app.integrations.sdk import BaseConfigFlow

class FitbitConfigFlow(BaseConfigFlow):
    domain = "fitbit"
    
    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Fitbit",
            "data_schema": {
                "type": "object",
                "properties": {
                    "sync_interval": {"type": "integer", "default": 15}
                }
            }
        }
        
    async def validate_input(self, user_input: dict) -> dict:
        return user_input
```

### 3. `provider.py`
The actual implementation. Must inherit from `BaseHealthProvider` (available in `app.integrations.sdk`). Use the `ObservationBuilder` to easily create FHIR-compliant data.

```python
from app.integrations.sdk import BaseHealthProvider
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration

class FitbitProvider(BaseHealthProvider):
    domain = "fitbit"
    
    async def pull_data(self, integration: UserIntegration) -> list[ObservationCreate]:
        # 1. State Management
        last_sync = self.get_sync_cursor(integration, "last_date", default="2024-01-01")
        
        # 2. Robust HTTP Client
        # Automatically handles 429 Rate Limits and raises IntegrationAuthError on 401
        # data = await self.fetch_json(f"https://api.fitbit.com/1/user/-/activities/heart/date/{last_sync}/today.json")
        
        # 3. Debugging (only logs if debug_mode is enabled)
        # self.log_debug_payload(integration, "Fitbit Data", data)

        builder = self.create_observation_builder(integration)
        observations = []
        
        # 4. Easily build Pydantic ObservationCreate schemas
        obs = (
            builder
            .set_biomarker("8867-4", "Heart rate")
            .set_value(72.0, "bpm", "{beats}/min")
            .set_reference_range(low=60, high=100)
            .build()
        )
        observations.append(obs)
        
        # Save cursor for next time
        self.set_sync_cursor(integration, "last_date", "2024-02-01")
        
        return observations
```

### 4. Enable it
By default, newly written integrations are invisible. A system administrator must execute a command or use a future admin UI to enable it globally.

```sql
INSERT INTO system_integrations (domain, is_enabled) VALUES ('fitbit', true);
```

## How It Works Under the Hood

1. **Startup**: FastAPI initializes the `IntegrationRegistry`. It scans folders, reads `manifest.json`, checks the DB for `is_enabled=True`, and loads the Python classes dynamically.
2. **Setup UI**: When a user clicks "Add Integration", the frontend calls `/api/v1/integrations/{domain}/config-flow`. The backend returns the JSON schema, and the frontend renders a form.
3. **Persistence**: The user's input (and potentially OAuth tokens) is securely saved in the `user_integrations` table under the `user_config` JSON column.
4. **Background Sync**: `celery_app.py` has a periodic beat that triggers `sync_active_integrations` in `tasks.py`. This task loops through active users, loads their specific provider instance, and calls `pull_data()` and `push_data()`.

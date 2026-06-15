# Health Assistant Bridge Python SDK

A lightweight Python SDK for building backend services, data scrapers, or cron jobs that securely push medical data into a self-hosted [Health Assistant](https://github.com/health-assistant-io/health-assistant) instance via the Universal Bridge integration.

## Installation

You can install this SDK locally directly from the source directory, or include it in your `requirements.txt`.

```bash
pip install -e .
```

## Features

- **Pydantic Models**: Provides strict type validation for the Universal Data Contract (`SyncPayload`, `ClientRecord`, etc.).
- **Synchronous Client**: Utilizes the popular `requests` library, perfect for simple scraping scripts.
- **Asynchronous Client**: Utilizes `httpx`, ideal for modern high-performance FastAPI or async data processing applications.

## Usage Example (Synchronous)

```python
import datetime
from health_assistant_bridge import (
    HealthAssistantBridgeClient, 
    SyncPayload, 
    ClientRecord, 
    MetricMappingRequest
)

# 1. Initialize the client using the User's credentials
client = HealthAssistantBridgeClient(
    base_url="https://my-health-assistant.local",
    integration_id="550e8400-e29b-41d4-a716-446655440000"
)

def sync_data():
    # 2. Check Status
    status = client.get_status()
    print(f"Last synced at: {status.cursor}")

    # (Your Code: Fetch new data from external API...)

    # 3. Optional: Ask AI to map unknown metric names
    mapping_request = [MetricMappingRequest(name="Natrium (Na)")]
    mappings = client.request_mapping(mapping_request)
    
    # 4. Push Data Using the Universal Contract
    payload = SyncPayload(
        client_version="1.0.0",
        source_system="python_scraper",
        cursor=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        records=[
            ClientRecord(
                type="quantitative",
                name="Sodium",
                biomarker_id="1234-uuid-from-mapping",
                code="2951-2",
                value=145.0,
                unit="mmol/L",
                timestamp="2024-08-10T00:00:00Z",
                coding_system="loinc"
            )
        ]
    )

    response = client.sync_data(payload)
    print(f"Successfully synced {response.metrics_synced} records!")

if __name__ == "__main__":
    sync_data()
```

## Usage Example (Asynchronous)

```python
import asyncio
from health_assistant_bridge import AsyncHealthAssistantBridgeClient

async def sync_data():
    client = AsyncHealthAssistantBridgeClient(
        base_url="https://my-health-assistant.local",
        integration_id="550e8400-e29b-41d4-a716-446655440000"
    )
    
    status = await client.get_status()
    print(status)

asyncio.run(sync_data())
```

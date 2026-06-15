# Health Assistant Bridge Client SDK

A lightweight TypeScript SDK for building browser extensions or mobile applications that securely push medical data into a self-hosted [Health Assistant](https://github.com/health-assistant-io/health-assistant) instance via the Universal Bridge integration.

## Installation

```bash
npm install @health-assistant/bridge-client
```
*(Note: Since this SDK might not be published to npm yet, you can also copy the `src/index.ts` file directly into your project, or install it via a local path/git submodule.)*

## Overview

The Bridge Client handles the three-step workflow required to sync unstructured external health portal data into standard FHIR records in Health Assistant:

1. **Get Status**: Retrieve the current connection status and the timestamp of the last successful sync.
2. **AI Ontology Mapping**: Ask the Health Assistant backend LLM to map unrecognized clinical metric names (e.g. localized strings like `"Νάτριο Ορού"`) to standardized English FHIR catalog entries (e.g. `"Sodium"`).
3. **Sync Data**: Push a strictly typed payload containing quantitative or categorical results.

## Usage Example

```typescript
import { HealthAssistantBridgeClient } from '@health-assistant/bridge-client';

// 1. Initialize the client using the User's credentials
// The user gets the Integration ID after setting up the bridge in their Health Assistant Dashboard
const client = new HealthAssistantBridgeClient(
  "https://my-health-assistant.local", // The backend URL
  "550e8400-e29b-41d4-a716-446655440000" // The unique integration ID
);

async function syncMyData() {
  // 2. Check Status
  const status = await client.getStatus();
  console.log(`Last synced at: ${status.cursor}`);
  
  // (Your code: Scrape data newer than the cursor from the hospital portal)
  const scrapedMetrics = [
    { rawName: "Natrium (Na)", value: 145, unit: "mmol/L" },
    { rawName: "Blood Type", stringValue: "A+" }
  ];

  // 3. Optional: Ask AI to map unknown metric names
  const mappingRequest = [
    { name: "Natrium (Na)" },
    { name: "Blood Type" }
  ];
  const mappings = await client.requestMapping(mappingRequest);
  
  // (Your code: Cache the mappings. Present them to the user for confirmation if desired.)
  const sodiumMapping = mappings.mappings.find(m => m.original_name === "Natrium (Na)");
  
  // 4. Push Data Using the Universal Contract
  const response = await client.syncData({
    client_version: "1.0.0",
    source_system: "my_hospital_extension",
    cursor: new Date().toISOString(),
    records: [
      {
        type: "quantitative",
        name: sodiumMapping?.new_biomarker_name || "Sodium",
        biomarker_id: sodiumMapping?.existing_biomarker_id,
        code: sodiumMapping?.new_biomarker_code || "2951-2",
        coding_system: sodiumMapping?.new_biomarker_coding_system || "loinc",
        value: 145.0,
        unit: "mmol/L",
        timestamp: "2024-08-10T00:00:00Z"
      },
      {
         type: "categorical",
         name: "Blood Type",
         value_string: "A+",
         timestamp: "2024-06-16T10:00:00Z"
      }
    ]
  });

  console.log(`Successfully synced ${response.metrics_synced} records!`);
}

syncMyData();
```

## API

### `new HealthAssistantBridgeClient(baseUrl, integrationId)`
Instantiates the client.
- `baseUrl`: The root URL of the Health Assistant backend (e.g. `http://localhost:8000`).
- `integrationId`: The UUID of the specific bridge instance, found in the Health Assistant Integrations UI.

### `client.getStatus()`
Returns a promise resolving to `BridgeStatus`. Use this to check connection validity and get the `cursor` timestamp.

### `client.requestMapping(metrics: MetricMappingRequest[])`
Pass an array of metric names. Returns an AI-generated array of `MappedMetric` objects suggesting how they align with the user's standardized FHIR catalog.

### `client.syncData(payload: SyncPayload)`
Pushes data into the backend. The payload must strictly conform to the `ClientRecord` types (Quantitative or Categorical).

# Health Assistant Bridge

The **Health Assistant Bridge** is a robust, general-purpose integration designed to connect headless clients—such as browser extensions or mobile applications—to the Health Assistant platform.

## Why this exists
In many scenarios, you need to scrape medical data from third-party portals (like National Health Systems, e.g., NHS, or proprietary hospital portals) that do not offer open APIs. The bridge acts as a universal adapter. The browser extension handles the scraping and parsing of unstructured, proprietary HTML/JSON, while the Bridge provides a secure, unified pipeline to map and store that data into your clinical records.

## Features
- **Adapter Pattern Architecture**: The backend requires zero hardcoded logic or parsers for specific portals. The client extension is responsible for scraping and converting data into the bridge's Universal Data Contract.
- **AI-Powered Ontology Mapping**: Not sure if a localized term like "Natrium" matches an existing catalog entry? The bridge provides an AI mapping endpoint to automatically align raw strings from the portal with your existing standard Biomarker definitions using LLMs.
- **Multiple Profiles**: Configure multiple bridge instances (e.g., one for yourself, one for a child). Each instance generates a unique URL containing the `integration_id`. This ID is securely bound to a specific Patient profile in Health Assistant. The extension simply pushes data to this URL, and the backend handles routing it to the correct patient without needing external identifiers like national ID numbers.

## Client Developer Guide

To build a client (Browser Extension/App), you need the base URL of the Health Assistant instance and the unique `integration_id` generated during the user's setup.

### The Recommended Workflow

1.  **Check Status**: `GET /status` to retrieve the `cursor` (last synced timestamp).
2.  **Scrape & Extract**: Extract data newer than the `cursor`.
3.  **Map New Metrics**: Use `POST /map` to ask the AI to map raw names (e.g., "Natrium") to standardized definitions. Ask the user to confirm the mapping in the extension UI, and save the mappings locally.
4.  **Sync Data**: Apply the mappings and send the Universal Client Payload to `POST /sync`.

---

### 1. Check Status
`GET /api/v1/integrations/health_assistant_bridge/api/<integration_id>/status`

Returns the current configuration and the last sync cursor.
```json
{
  "status": "active",
  "integration_id": "uuid-here",
  "last_synced_at": "2024-06-15T12:00:00Z",
  "cursor": "2024-06-15T12:00:00Z"
}
```

### 2. Request AI Mapping
`POST /api/v1/integrations/health_assistant_bridge/api/<integration_id>/map`

Send a list of raw, unrecognized metric names. The backend uses its LLM to map them against the patient's existing catalog or proposes a new standardized LOINC/Custom definition.

**Request:**
```json
{
  "unmapped_metrics": [
    {"name": "Natrium (Na)", "code": null},
    {"name": "HCT", "code": null}
  ]
}
```

**Response:**
```json
{
  "mappings": [
    {
      "original_name": "Natrium (Na)",
      "action": "map_to_existing",
      "existing_biomarker_id": "uuid-of-sodium-record",
      "new_biomarker_name": null,
      "new_biomarker_code": null,
      "new_biomarker_coding_system": "loinc"
    },
    {
      "original_name": "HCT",
      "action": "create_new",
      "existing_biomarker_id": null,
      "new_biomarker_name": "Hematocrit",
      "new_biomarker_code": "20570-8",
      "new_biomarker_coding_system": "loinc"
    }
  ]
}
```
*Note: The client should present these mappings to the user for confirmation and cache them locally.*

### 3. Push Data (The Universal Contract)
`POST /api/v1/integrations/health_assistant_bridge/api/<integration_id>/sync`

Send the transformed payload using the established mappings.

**Request:**
```json
{
  "client_version": "1.1.0",
  "source_system": "health_portal_extension",
  "cursor": "2024-12-29T15:01:48Z",
  "records": [
    {
      "type": "quantitative",
      "code": "uuid-of-sodium-record", 
      "coding_system": "loinc",
      "name": "Sodium",
      "value": 145.0,
      "unit": "mmol/L",
      "timestamp": "2024-08-10T00:00:00Z",
      "reference_range": {
        "low": 137.0,
        "high": 147.0
      },
      "interpretation": "INSIDE_LIMIT",
      "performer": "City General Hospital Laboratory"
    },
    {
      "type": "categorical",
      "code": "blood_type",
      "coding_system": "custom",
      "name": "Blood Type",
      "value_string": "A+",
      "timestamp": "2024-06-16T10:00:00Z"
    }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "metrics_synced": 2,
  "message": "Data synchronized successfully"
}
```

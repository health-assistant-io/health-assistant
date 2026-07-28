# Telemetry & TimescaleDB Architecture

This document tracks the design decisions and architecture for handling high-frequency health data (IoT devices, wearables, continuous monitors) in Health Assistant.

## The Problem
Standard clinical data maps beautifully to the HL7 FHIR `Observation` model. A blood test taken once every 3 months is easily stored in the `fhir_observations` PostgreSQL table.
However, devices like the Apple Watch, Oura Ring, or continuous glucose monitors (CGMs) can generate health measurements every 1 to 5 minutes. Routing this data into standard FHIR structures results in massive table bloat, drastically degrading dashboard performance and slowing down routine clinical queries.

## The Solution: Split Architecture
Health Assistant implements a frequency-based routing architecture:

1. **Low-Frequency (Clinical) Data -> FHIR Observations**
   - Stored in standard `fhir_observations` table.
   - Used for blood panels, point-in-time weight, diagnostic results.

2. **High-Frequency (Telemetry) Data -> TimescaleDB**
   - Stored in the `telemetry_data` hypertable.
   - Extremely high ingestion limits and heavily compressed.

## Dynamic Routing via Biomarker Definitions
Instead of hardcoding which metrics go to which database, routing is configurable.
The `biomarker_definitions` table includes an `is_telemetry` boolean flag.
When integration webhooks push data to the backend, the parser resolves the data to a standard biomarker. The core sync engine then inspects this flag:
- If `is_telemetry = true`: Data is mapped to a `TelemetryDataModel` and saved to TimescaleDB.
- If `is_telemetry = false`: Data is mapped to an `Observation` and saved to FHIR.

**Automated Data Migration:** System administrators can toggle this flag via the UI, making the system infinitely expandable for future IoT devices without requiring code changes (see "Long-Format Storage" below for why this is now truly zero-code). When the flag is toggled on an existing biomarker, the Celery task `migrate_biomarker_data` performs an automatic, batched (5000 rows) migration between the standard PostgreSQL `fhir_observations` table and the TimescaleDB `telemetry_data` hypertable.

**Telemetry → FHIR patient attribution:** every telemetry row carries a
persisted `patient_id` column (populated at insert by the integration
pipeline, the FHIR→telemetry migration direction, and the `/telemetry/data`
upload endpoint). When an admin flips `is_telemetry` true→false on a
populated biomarker, the migration task resolves the patient per row
directly from `tr.patient_id` — no device-id attribution chain needed.

For rows that lack a persisted `patient_id` (e.g. legacy mobile uploads
that didn't send one), the single-patient-tenant fallback remains the safe
default. Rows that can't be attributed (no `patient_id` + multi-patient
tenant) are **skipped**, not silently assigned to a random patient. The
count is exposed in `BiomarkerDefinition.meta_data["migration_skipped_no_patient"]`.

## Long-Format Storage
The `telemetry_data` hypertable is **long-format**: one row per
`(timestamp, device, slug)` triple with explicit `value Float` + `unit Text`
columns. This is the canonical TimescaleDB / InfluxDB / Prometheus pattern
for variable-schema metrics.

| Column | Type | Notes |
|---|---|---|
| `tenant_id` | UUID | No FK (TimescaleDB limitation); cleanup job purges on tenant delete. |
| `id` | UUID | Composite PK part (required by TimescaleDB). |
| `timestamp` | timestamptz | Composite PK part; hypertable time dimension. |
| `device_id` | String | Source device / integration instance. |
| `slug` | String | Biomarker slug (`heart-rate`, `steps`, `spo2`, `glucose`, …). |
| `value` | Float | NOT NULL — the numeric measurement. |
| `unit` | String | Optional unit symbol (`bpm`, `count`, `%`). |
| `patient_id` | UUID | Persisted attribution (populated at insert). |

Why long-format (vs. the legacy wide+JSONB hybrid with dedicated
`heart_rate`/`steps`/`calories` columns):

- **Adding a new telemetry biomarker is a row-only change.** Flip
  `BiomarkerDefinition.is_telemetry = True` — no DDL, no service-layer
  branching, no new column. SpO2 / CGM / sleep stages are first-class
  citizens alongside heart rate.
- **Generic continuous aggregates.** One `GROUP BY slug` CAgg definition
  covers every current and future telemetry biomarker.
- **Range queries get a real B-tree.** `WHERE value > 120` prunes via
  `(tenant_id, slug, timestamp)` instead of needing per-metric JSONB
  functional indexes.

The previous `_METRIC_COLUMNS` alias map, the slug→column branching in 5
files, and the JSONB `data` catch-all are gone. See migration
`t1e2l3o4n5g6_telemetry_long_format` for the full rationale.

## Preserving Spikes (OHLC Aggregation)
When rendering a dashboard for "The Last 6 Months", fetching minute-by-minute heart rate data would crash a user's browser.
Data must be downsampled. However, taking a pure mathematical `AVERAGE()` hides critical clinical data (e.g., a dangerous 10-minute spike to 190 bpm).

To solve this, the `AnalyticsService` uses **TimescaleDB OHLC (Open-High-Low-Close) Aggregations**:
- `time_bucket_gapfill()` ensures missing days (e.g. user forgot to wear the watch) are returned as `NULL` rather than connected across the graph.
- The query returns `AVG()`, `MIN()`, and `MAX()` for each bucket.
- The frontend renders this as an average line with a shaded variance envelope (Min to Max), guaranteeing that the absolute highest spike during that timeframe remains visibly represented on the chart.

**Decoupled Aggregation Resolution:**
The system decouples the "Temporal Scope" (e.g., viewing the Last 30 Days) from the "Aggregation Bucket" (e.g., grouping by 1-hour averages). Users can dynamically adjust the resolution density directly from the UI dropdowns, and the backend securely handles the dynamic PostgreSQL `INTERVAL` casting (e.g. `1 minute`, `15 minutes`, `1 day`, `1 week`).

When the requested bucket exactly matches a continuous aggregate's resolution,
the analytics service transparently dispatches to the CAgg (pre-computed,
fast on long horizons); otherwise it falls back to live
`time_bucket_gapfill()` over the raw hypertable. The CAgg path filters
`WHERE slug = :slug` so every telemetry biomarker is covered by one
definition.

## Implemented optimizations (TimescaleDB)

The hypertable is paired with production-grade TimescaleDB features, both
registered in the consolidated baseline (`alembic/versions/8ddb7ef7ca4d_consolidated_baseline.py`)
and rebuilt long-format in migration `t1e2l3o4n5g6_telemetry_long_format`:

- **Generic continuous aggregates**: three materialized views —
  `telemetry_hourly`, `telemetry_daily`, `telemetry_monthly` — pre-compute
  rollups in the background via `add_continuous_aggregate_policy`. Each
  groups by `slug`, so one definition covers every current and future
  telemetry biomarker (no DDL when a new biomarker is flagged
  `is_telemetry=True`). The monthly CAgg covers the `last-12-months` /
  `all-time` analytics buckets that previously hit the raw hypertable.
  The `AnalyticsService` (`backend/app/services/analytics_service.py`)
  transparently dispatches to these CAggs when the requested bucket is
  `1 hour`, `1 day`, or `1 month`.
- **Compression policy**: `add_compression_policy('telemetry_data', INTERVAL '7 days')`
  compresses raw minute-by-minute data older than one week
  (`compress_segmentby = 'tenant_id, device_id, slug'`,
  `compress_orderby = 'timestamp DESC'`). Segmenting by the query-pruning
  keys means both tenant-wide and per-device queries skip irrelevant
  compressed chunks.
- **Retention policy**: `add_retention_policy('telemetry_data', INTERVAL '2 years')`
  auto-prunes raw rows older than 2 years.

## Upload Contract
The `POST /api/v1/telemetry/data` endpoint accepts long-format points — one
per metric/timestamp:

```json
{
  "device_id": "apple_watch_1",
  "points": [
    {"timestamp": "2026-07-28T08:00:00Z", "slug": "heart-rate", "value": 72.0, "unit": "bpm"},
    {"timestamp": "2026-07-28T08:00:00Z", "slug": "steps", "value": 12.0},
    {"timestamp": "2026-07-28T08:05:00Z", "slug": "heart-rate", "value": 75.0, "unit": "bpm"}
  ]
}
```

A mobile client syncing N metrics at one timestamp sends N points (not one
point with N fields). The integration SDK already produces one Observation
per metric, so the mapping is 1:1. The service uses SQLAlchemy Core bulk
insert with `ON CONFLICT DO NOTHING` chunked at 5000 rows for high-frequency
ingest.

## Future Considerations (Roadmap)
- **FHIR Interoperability Boundary:** The split architecture inherently moves high-frequency telemetry data outside of strict FHIR compliance. Currently, when exporting patient records to FHIR, telemetry data is excluded. Future versions will need to dynamically downsample and map TimescaleDB data back into FHIR `Observation` bundles during export.
- **Continuous-aggregate-based anomaly detection:** `get_telemetry_anomalies` still runs the detector in Python over raw rows; SQL-side downsampling via the CAggs is a separate improvement.

## Unified Clinical View (UI & AI Integration)
Despite the data being physically split across two different database engines, both the frontend and the AI Chatbot provide a unified longitudinal view.

1. **Frontend:** The `BiomarkerDetail` view uses the `AnalyticsService` to merge and sort FHIR and TimescaleDB data.
2. **AI Chatbot:** The AI Assistant uses the `get_aggregated_biomarker_trends` tool, which routes through the same `AnalyticsService` logic. This ensures the AI can reason over high-frequency wearable data (steps, heart rate, SpO2) without being overwhelmed by raw records, while strictly adhering to aggregated OHLC (Average, Min, Max) values.

This is achieved dynamically by the `AnalyticsService` in the backend:
1. **FHIR Data:** The service queries standard `fhir_observations` matching the `biomarker_id`.
2. **Telemetry Data:** The service queries the long-format hypertable (or the matching continuous aggregate) with `WHERE slug = :slug` — uniform across every telemetry biomarker.
3. **Merge & Sort:** The backend formats both datasets into the identical JSON response structure, sorts them chronologically by timestamp, and returns them to the requester (React chart or AI Tool) as a single cohesive longitudinal record.

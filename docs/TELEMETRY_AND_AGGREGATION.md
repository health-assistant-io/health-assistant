# Telemetry & Analytics Architecture

How Health Assistant handles high-frequency health data (wearables, IoT
devices, continuous monitors): one long-format hypertable, a frozen continuous
aggregate ladder, a device registry, canonical units, and one analytical
contract shared by the web dashboard and the mobile bridge.

## The problem

Standard clinical data maps cleanly to the HL7 FHIR `Observation` model: a
blood test every three months is one row in `fhir_observations`. Devices such
as a smartwatch, ring, or continuous glucose monitor can emit a measurement
every few minutes. Routing that into FHIR structures bloats the table and slows
routine clinical queries.

## The split

Health Assistant routes by measurement frequency:

1. **Low-frequency clinical data → FHIR Observations** (`fhir_observations`):
   lab panels, point-in-time weight, diagnostic results.
2. **High-frequency telemetry → TimescaleDB** (`telemetry_data`): wearable and
   device streams, compressed and chunked.

Routing is data, not code. `biomarker_definitions.is_telemetry` decides where a
mapped observation belongs. When an admin flips that flag, the Celery task
`migrate_biomarker_data` moves existing rows between the two stores in batches;
every telemetry row carries a persisted `patient_id`, so the migration never
needs a device→user→patient attribution chain.

## Long-format storage

`telemetry_data` is a TimescaleDB hypertable in **long format**: one row per
`(timestamp, device, slug)` triple. This is the canonical pattern for
variable-schema metrics (TimescaleDB, InfluxDB, Prometheus).

| Column | Type | Notes |
|---|---|---|
| `tenant_id` | UUID | No FK (hypertable limitation); tenant cleanup owns the cascade. |
| `id` | UUID | Composite PK part (required by TimescaleDB). |
| `timestamp` | timestamptz | Composite PK part; hypertable time dimension. |
| `device_id` | UUID | Points at `telemetry_devices.id`; no FK (chunked table). |
| `slug` | text | `BiomarkerDefinition.slug`; the metric discriminator. |
| `value` | double precision | Canonical measurement (NOT NULL). |
| `unit` | text | Canonical unit symbol. |
| `raw_value` | double precision | Exactly what the source sent (NOT NULL). |
| `raw_unit` | text | Source unit symbol. |
| `patient_id` | UUID | Persisted attribution (NOT NULL); no FK, same cleanup contract. |

Why long format:

- **A new telemetry metric is a row-only change.** Add or flip a
  `BiomarkerDefinition`; no column, no table, no service-layer branch.
- **One generic aggregate definition** covers every current and future metric.
- **Range predicates use a real B-tree** (`tenant_id`, `slug`, `timestamp`)
  instead of per-metric JSONB functional indexes.

The previous wide+JSONB shape and the `_METRIC_COLUMNS` alias map are gone.

## Device registry

`telemetry_devices` replaces free-string device ids:

| Column | Notes |
|---|---|
| `id` | UUID referenced by `telemetry_data.device_id`. |
| `tenant_id` | NOT NULL, FK to `tenants` (cascade). |
| `patient_id` | NOT NULL, FK to `fhir_patients` (cascade). |
| `source` | Device/source family (for example `health-connect`, `bridge`, `fhir_import`). |
| `external_ref` | Opaque client reference (watch serial, integration instance). |
| `label` | Human-readable name. |

Identity is `(tenant_id, patient_id, source, external_ref)`: the same hardware
bound to two patients is two rows, and two sources may reuse a reference
without colliding. Ingest resolves or creates the row; the hypertable cannot
carry the FK, so tenant/patient cleanup is the erasure contract.

## Canonical units and provenance

Ingest stores the **canonical** measurement in `value`/`unit` and the source
measurement in `raw_value`/`raw_unit`. Canonicalization targets the
biomarker's preferred unit through one explicit conversion table
(`app/services/biomarkers/unit_conversion.py`): temperature, common mass/length units, and
glucose's mass↔molar pair. A pair the table does not declare keeps the source
unit as canonical — guessing would silently corrupt aggregation.

Because provenance is stored, a conversion-table fix recomputes canonical
values from `raw_value`/`raw_unit` without asking devices to re-ingest (peer
review 2026-10-21, item 9). Mixed-unit series therefore aggregate correctly by
construction: 98.6 °F and 37.0 °C both store as 37.0 °C.

## The continuous-aggregate ladder

The ladder is a frozen decision, created once by `0002_telemetry`:

- `telemetry_hourly` — `time_bucket('1 hour')`
- `telemetry_daily` — `time_bucket('1 day')`
- `telemetry_monthly` — `time_bucket('1 month')`

Every view groups by `(tenant_id, device_id, patient_id, slug)` and computes
`avg/min/max/sum/count`, so one definition covers every current and future
telemetry biomarker. All three are **realtime** (`materialized_only = false`):
buckets newer than the refresh watermark union the live hypertable, so
dashboards never wait for a background refresh.

Declared with the ladder, not sprinkled across revisions:

- a **refresh policy** per view (hourly: 3-day lookback; daily: 7 days;
  monthly: 12 months);
- the hypertable **retention policy** (`drop_chunks` after 2 years);
- an explicit **7-day chunk interval** (a policy change, not a schema change);
- **compression** after 7 days, segmented by `tenant_id, device_id, slug`.

`0005_telemetry_unified` re-installs the identical ladder after rewriting the
base table's `device_id` type (a TimescaleDB type-change requirement); the
ladder shape itself did not change.

## One analytical contract

- **Web:** `/api/v1/analytics/*` is the single web-facing read surface —
  dashboard, summary, trends/series, reference ranges, categories, and
  anomalies.
- **Bridge:** the mobile bridge reads telemetry only through its
  schema-pinned sub-API (`GET /integrations/health_assistant_bridge/schema`):
  `observations` (series) and `observations/latest` (latest per biomarker).
  The generated-schema drift gate pins that surface.
- **Ingest:** `POST /api/v1/telemetry/data` is the one ingest seam.
- The old `/telemetry/data`, `/telemetry/data/summary`, and
  `/telemetry/anomalies` read routes are deleted; there is no second read
  shape over the same data.

## One anomaly engine

Anomaly detection runs SQL-side over `telemetry_hourly` (rolling statistics),
never over raw rows in Python. For each `(tenant, patient, device, slug)` series
the latest bucket is compared with up to 24 preceding buckets; a |z-score|
above the threshold becomes a `spike` or `drop` event.

Events persist to `telemetry_anomalies`:

| Column | Notes |
|---|---|
| `tenant_id`, `patient_id` | Attribution; patient FK cascades. |
| `device_id` | Device UUID (same cleanup contract as the hypertable). |
| `slug` | Biomarker slug. |
| `window_start`, `window_end` | The detected bucket window. |
| `kind` | Closed vocabulary: `spike` / `drop`. |
| `z_score`, `delta` | The statistics that fired. |
| `detected_at`, `resolved_at` | Event lifecycle; recovery closes the row. |
| `payload` + `payload_schema_version` | Open diagnostics document (baseline statistics), not a query surface. |

The dedup key is
`UNIQUE (tenant_id, patient_id, device_id, slug, window_start, kind)`. Patient
**and** device are in the key on purpose: two patients — or two devices —
sharing a slug must never collide and silently lose one event. Re-running
detection updates the event instead of duplicating it; a newer bucket resolves
the old event, so recovery is recorded. `GET /analytics/anomalies` reads the
persisted events.

## Preserving spikes (OHLC charts)

A pure average hides a dangerous ten-minute spike. The analytics query uses
TimescaleDB `time_bucket_gapfill()` and returns `AVG`, `MIN`, and `MAX` per
bucket; the frontend renders an average line with a min–max envelope, so the
absolute high and low remain visible. Gap-fill emits `NULL` for missing buckets
instead of connecting across a gap.

Aggregation resolution is decoupled from the time range. When the requested
bucket matches a CAgg (`1 hour`, `1 day`, `1 month`) the service reads the
aggregate; otherwise it gap-fills the raw hypertable. The `INTERVAL` literal is
guarded by a strict allow-list.

## Upload contract

`POST /api/v1/telemetry/data` accepts long-format points — one per
metric/timestamp:

```json
{
  "device_id": "apple_watch_1",
  "source": "health-connect",
  "device_label": "Apple Watch",
  "points": [
    {"timestamp": "2026-07-28T08:00:00Z", "slug": "heart-rate", "value": 72.0, "unit": "bpm", "patient_id": "…"},
    {"timestamp": "2026-07-28T08:00:00Z", "slug": "steps", "value": 12.0, "patient_id": "…"}
  ]
}
```

Every slug must resolve to a visible `BiomarkerDefinition` flagged
`is_telemetry`; an unknown or non-telemetry slug is a typed 400 rejection. The
service resolves the device row, canonicalizes each measurement, and bulk
inserts chunks of 5000 with `ON CONFLICT (id, timestamp) DO NOTHING` so a
retried batch is safe.

## Unified clinical view

FHIR and telemetry stay physically separate but read as one longitudinal view:

1. **Frontend:** the biomarker detail and trends views query
   `AnalyticsService`, which merges and sorts both sources.
2. **AI:** the `get_aggregated_biomarker_trends` tool uses the same service, so
   the assistant reasons over OHLC aggregates rather than raw device rows.
3. **Bridge:** `observations/latest` merges FHIR and telemetry per biomarker
   and dedups cross-source residue (newest wins).

## Decisions kept open

- **FHIR export excludes telemetry.** FHIR R4 has no lossless representation
  of minute-resolution device streams; downsampling them into `Observation`
  bundles would be lossy and misleading. The split is intentional, and
  backups keep telemetry in a dedicated sidecar.
- **Patient-scope export excludes telemetry.** Telemetry rows are
  tenant-scoped in the backup sidecar; patient-scope exports note the
  exclusion instead of silently dropping rows.

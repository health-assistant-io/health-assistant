# Pull (Streams, Cursors & Canonical Units)

## What a sync does

Each sync cycle (the instance's interval, or a manual **Sync now**)
refreshes the access token if it is expired or about to expire, then runs
one cursor-scoped measure pull and funnels every result through the
platform's single ingest path:

| Pull | Cursor key | Stream | Lands in |
|---|---|---|---|
| Withings `measure` groups | `measure` | telemetry | Telemetry store (Biomarker Engine) |

The cursor is the newest group creation stamp seen: the next sync only
asks for groups created after the last success. The first sync pulls the
configured initial window (default 90 days).

## Where each measurement lands

| Withings measure type | Catalog code | Canonical unit |
|---|---|---|
| Weight | LOINC 29463-7 | kg |
| Body fat percentage | LOINC 41982-0 | % |
| Systolic blood pressure | LOINC 8480-6 | mmHg |
| Diastolic blood pressure | LOINC 8462-4 | mmHg |
| Heart rate | LOINC 8867-4 | bpm |
| SpO2 | LOINC 59408-5 | % |
| Body temperature | LOINC 8310-5 | °C |

The ingest funnel resolves each value against the catalog
(`BiomarkerDefinition`) and stores it with **canonical units**: the
telemetry row keeps the canonical value/unit pair plus the untouched
source pair as provenance, and the device that reported the measurement
is registered (or reused) in the tenant's device registry.

## Dedup — re-syncs write nothing twice

Identical measurements are recognized and skipped, at two layers:

- **Cursor**: the next pull only asks for groups created after the last
  success, so an unchanged account yields nothing to write.
- **Dedup keys**: if the same measurements come back anyway (a cursor
  reset, an expanded window), the ingest funnel drops them on the natural
  key (patient, time, metric, device). A full re-pull writes zero
  duplicates.

## Unmapped measure types are kept, never dropped

A measurement whose type has no catalog mapping is still stored — as a
raw observation carrying the Withings measure type — and the sync log
records the gap. Defining the biomarker later maps future pulls; the raw
rows remain as the historical record. Static anthropometrics (height) are
excluded on purpose and counted, not gap-recorded.

## Hostile payloads are rejected, typed

Measure groups that are grossly oversized or have the wrong shape are
rejected before mapping, counted, and reported in the sync's debug log —
a bad record never aborts the sync and never lands as a corrupted row.

## Sleep and activity

Not wired yet (see [Overview](overview.md)) — no catalog mappers exist
for the sleep/activity series, so the connector does not pull them.

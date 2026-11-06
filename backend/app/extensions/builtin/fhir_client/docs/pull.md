# Pull (Streams & Cursors)

## What a sync does

Each sync cycle (the instance's interval, or a manual **Sync now**) runs
four independent, cursor-scoped searches against the remote server and
funnels every result through the platform's single ingest path:

| Search | Cursor key | Stream | Lands in |
|---|---|---|---|
| `Observation?patient=…` | `Observation` | observations | Biomarker Engine |
| `DiagnosticReport?patient=…` | `DiagnosticReport` | labs | Labs |
| `MedicationStatement?patient=…` | `MedicationStatement` | observations | Medications |
| `Encounter?patient=…` | `Encounter` | appointments | Examinations |

Every search is a `_lastUpdated` delta: the first sync pulls the
configured initial window (default 12 months), after that only resources
updated since the last success. Each resource class has its **own
cursor**, so a large lab backlog never delays medication pulls.

## Dedup — re-syncs write nothing twice

Identical records are recognized and skipped, at two layers:

- **Cursor**: the next search only asks for resources updated after the
  last success, so an unchanged server yields nothing to write.
- **Dedup keys**: if the same records come back anyway (a cursor reset,
  an expanded window), the ingest funnel drops them — observations and
  lab reports on their natural key (patient, time, code), medications and
  examinations on the remote resource id. A full re-pull writes zero
  duplicates.

## Unmapped codes are kept, never dropped

A lab result whose LOINC code has no catalog mapping is still stored —
as a raw observation — and the sync log records the gap with the code.
Approving the code later (the platform's biomarker proposal flow)
maps future pulls; the raw rows remain as the historical record.

## Hostile resources are rejected, typed

Resources that are grossly oversized or have the wrong shape are rejected
before mapping, counted, and reported in the sync's debug log — a bad
record never aborts the sync and never lands as a corrupted row.

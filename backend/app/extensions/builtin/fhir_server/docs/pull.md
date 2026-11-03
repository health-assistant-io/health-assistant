# Pull — Full Patient Record

The pull syncs the **full patient record** from the remote FHIR server into Health Assistant — not just observations. Each remote resource type lands through the same canonical write service the UI uses, with the same RBAC, audit trail, and dedup.

## What gets pulled

The **Record Types to Pull** (`pull_resources`) config selects which resource types an instance ingests. All are enabled by default; remove one to opt a specific instance out of it.

| Remote FHIR resource | Ingestion target | Dedup key |
|----------------------|------------------|-----------|
| `Observation` | Biomarker Engine (labs / vitals) | by content (biomarker mapping) |
| `Condition` | Health Journeys (clinical events) | `(tenant, patient, integration, Condition.id)` |
| `Encounter` | Examinations (visits) | `(tenant, patient, integration, Encounter.id)` |
| `DocumentReference` | Documents → OCR + LLM extraction | `(tenant, patient, integration, DocumentReference.id)` |
| `MedicationStatement` / `MedicationRequest` | Medications (via the `intent` discriminator) | per remote id |
| `AllergyIntolerance` | Allergies | per remote id |
| `Immunization` | Immunizations (vaccine doses) | per remote id |

`Observation` is always pulled when the direction allows it (it's the core feed into the Biomarker Engine); the `pull_resources` selection gates only the additional resource types.

## Per-resource cursors

Each resource type has its own `_lastUpdated` cursor (`last_updated:Condition`, `last_updated:Encounter`, …), so one slow resource can't starve the others. A pull runs:

```
GET /{Resource}?patient=<remote>&_lastUpdated=gt<cursor>&_count=100&_sort=_lastUpdated
```

and follows Bundle `link[rel=next]` pagination (capped at 50 pages). After a successful pull, the cursor advances past the newest row seen — so subsequent pulls are incremental. **Reset Cursors** clears every cursor so the next sync re-pulls the full configured window.

## What each resource becomes

### Observation → Biomarker Engine

Labs and vitals map to `ObservationCreate` on the local patient, then through the Biomarker Engine — which resolves (or auto-creates) a `BiomarkerDefinition` by LOINC code, name, slug, or aliases, normalizes units, and computes a relative score. Multi-component observations (e.g. blood pressure `85354-9`), `note[]`, `referenceRange[]`, and the canonical `category` list are preserved end-to-end. Routing to FHIR vs TimescaleDB telemetry follows the biomarker's `is_telemetry` flag.

### Condition → Health Journeys

A `Condition` becomes a clinical event (a Health Journey). `clinicalStatus` maps to the event status; the code's text becomes the title; `onsetDateTime` / `abatementDateTime` become the onset/resolved dates; the remote `Condition.id` is the dedup key.

### Encounter → Examinations

An `Encounter` becomes an examination (a visit). `period.start` → the exam date; `reasonCode` / `type` → the notes; `class.code` → a category hint. The remote `Encounter.id` is the dedup key, and it's also used to link DocumentReferences pulled in the same sync (see below).

### DocumentReference → Documents + OCR

Each `DocumentReference` attachment is fetched (absolute URLs and `<base>/Binary/{id}` are resolved against the server) and routed through the **same OCR + LLM extraction pipeline** as a UI upload. If the `DocumentReference.context.encounter` references an `Encounter` that was just pulled, the document is auto-linked to that examination. The remote `DocumentReference.id` is the dedup key (DB-layer dedup — a re-pull of the same upstream file is a no-op, no re-OCR). Per-sync caps protect RAM: at most 20 documents and 50 MiB total per sync.

### MedicationStatement / MedicationRequest → Medications

Both map to the local `Medication` row via the `intent` discriminator — a `MedicationStatement` becomes `intent=statement`, a `MedicationRequest` becomes `intent=order` (so each projects back to the right FHIR resource on the facade). The medication code, status, dosage, timing, and reason are extracted from the remote resource. The remote resource id is the dedup key.

### AllergyIntolerance → Allergies

Maps to the local allergy record: code, clinical/verification status, category, criticality, onset, and structured reactions. The remote `AllergyIntolerance.id` is the dedup key.

### Immunization → Immunizations

Maps to a vaccine-dose record: vaccine code, status, occurrence date, dose number, lot number, manufacturer, and location. The remote `Immunization.id` is the dedup key.

## Dedup

For every resource type except `Observation`, the engine stamps `source_integration_id` (= the integration's own id; the provider can't fake it) and the provider sets `external_id` (= the remote resource's stable id). A partial unique index on `(tenant_id, patient_id, source_integration_id, external_id)` catches duplicates at the DB layer — a re-sync never creates a duplicate, and the narrow race window between SELECT and INSERT is handled with `IntegrityError` recovery.

## sync_direction gating

The pull hooks honor `sync_direction`: in `push_only` or `none`, they return empty (no remote calls). The **Pull Now** action bypasses this for explicit manual control.

## See also

- [Push — Outbound Observations](push.md)
- [Catalog Proposals](catalog-proposals.md) (what happens to unmapped codes)
- [Selecting the Remote Patient](patient-selection.md)

# State Biomarkers (Qualitative Values)

Not every biomarker is a number. Some results are categorical states: *Positive / Negative / Detected / Susceptible / Within Limits / Indeterminate*. Health Assistant models these as **state biomarkers** — a first-class value type alongside the numeric (quantity) default — with FHIR-native storage, a controlled vocabulary, and the same history tracking as numeric biomarkers.

This doc covers when to use state biomarkers, how to declare them, how values flow through the system, and the FHIR R4 search semantics.

## When to use STATE vs QUANTITY

Every `BiomarkerDefinition` carries a `value_type` discriminator:

| value_type | Stored as | Use for | Example biomarkers |
|---|---|---|---|
| `quantity` (default) | `Observation.value_quantity` (JSONB `{value, unit}`) | Numeric measurements with units and reference ranges | Glucose, cholesterol, heart rate, blood pressure |
| `state` | `Observation.value_codeableConcept` (FHIR R4 CodeableConcept) | Categorical results drawn from a controlled vocabulary | SARS-CoV-2 PCR, microbiology cultures, antibiotic susceptibility, dipstick results |

A STATE biomarker carries **no unit and no numeric reference range**. Instead it declares an `allowed_states` set, with one or more states marked `is_normal=True` (the "normal set" that replaces numeric reference ranges).

## The state catalog

State codes are drawn from standard code systems so FHIR interop is immediate — no proprietary vocabulary invented. The catalog is **universal** (no tenant override): a state code means the same thing in every installation.

| Code system | URL | Used for |
|---|---|---|
| HL7 v3-ObservationInterpretation | `http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation` | POS, NEG, IND, S/R/I (susceptibility), H/L/N (interpretation), E (equivocal), WR (weakly reactive) |
| SNOMED CT | `http://snomed.info/sct` | 260373001 Detected, 260415000 Not detected, 52101004 Present, 272519000 Absent |
| FHIR DataAbsentReason | `http://terminology.hl7.org/CodeSystem/data-absent-reason` | unknown, not-performed, inconclusive, not-applicable |
| Custom | `urn:uuid:health-assistant:custom-state` | WITHIN_LIMITS / ABOVE_LIMIT / BELOW_LIMIT / TRACE / STRONGLY_REACTIVE |

The catalog is seeded from `backend/data/seeds/biomarker_states.json` (22 codes covering microbiology, serology, susceptibility, qualitative presence, regulatory limits, and data-absent). Edit the JSON and restart to extend it; the seed is idempotent.

## Declaring a STATE biomarker

A STATE biomarker is a regular `BiomarkerDefinition` with `value_type=state` and an `allowed_states` list. Each entry references a state by slug and marks whether it belongs to the normal set:

```json
POST /api/v1/biomarkers/
{
  "slug": "sars-cov-2-pcr",
  "name": "SARS-CoV-2 PCR",
  "value_type": "state",
  "allowed_states": [
    {"state_slug": "positive", "is_normal": false},
    {"state_slug": "negative", "is_normal": true},
    {"state_slug": "indeterminate", "is_normal": false}
  ]
}
```

**Cross-field invariants** (enforced at the schema, endpoint, and DB CHECK constraint layers):

- STATE biomarkers cannot be telemetry — telemetry values are stored in a Float NOT NULL TimescaleDB hypertable. The DB CHECK constraint `ck_biomarker_definitions_state_not_telemetry` rejects any row with `value_type='state' AND is_telemetry=TRUE`.
- STATE biomarkers carry no `preferred_unit_id` — categorical values are unitless. Enforced by `ck_biomarker_definitions_state_no_unit`.
- STATE biomarkers must declare at least one `allowed_state`.
- `value_type` cannot be flipped on PATCH — that would strand existing observations. Drop and recreate the definition instead.

## Multi-state panels (component[])

Some categorical results carry many states at once — a microbiology culture reports one state per organism, an antibiotic panel reports susceptible/resistant per drug. FHIR R4 models this as `Observation.component[]`.

A STATE biomarker with `supports_multi_state=True` accepts panel-style observations:

```json
POST /api/v1/observations
{
  "biomarker_id": "<wound-culture-panel-id>",
  "code": {"text": "Wound culture"},
  "subject": {"reference": "Patient/<id>"},
  "component": [
    {
      "code": {"coding": [{"code": "staph-aureus"}]},
      "valueCodeableConcept": {"coding": [{"code": "POS", "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"}]}
    },
    {
      "code": {"coding": [{"code": "e-coli"}]},
      "valueCodeableConcept": {"coding": [{"code": "NEG", "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"}]}
    }
  ]
}
```

Each component entry carries its own `code` (the sub-context — e.g. organism name) and a `valueCodeableConcept` drawn from the parent biomarker's `allowed_states`. The single-state contract (one top-level `valueCodeableConcept`) is rejected when `supports_multi_state=True`; conversely, `component[]` is rejected when `supports_multi_state=False`.

## The hard value-shape contract

Every Observation write path — REST create, OCR pipeline, integration sync, FHIR import — runs through a single validator (`app/services/observation_value_validator.py`) before the row is persisted. Mismatches raise `InvalidObservationValue` → HTTP 422.

| Biomarker `value_type` | `supports_multi_state` | Required value shape |
|---|---|---|
| `quantity` | n/a | `value_quantity` present; no top-level `valueCodeableConcept` |
| `state` | `false` (default) | Exactly one top-level `valueCodeableConcept` whose `coding[0].{code, system}` is in the biomarker's `allowed_states` |
| `state` | `true` | `component[]` with ≥2 entries; each entry carries `code` + `valueCodeableConcept` validated against `allowed_states` |

A STATE biomarker rejects `value_quantity` and `value_string` outright. A QUANTITY biomarker rejects a top-level `valueCodeableConcept`. The validator is a no-op when `biomarker_id` is unset (the auto-create path can't enforce a contract that hasn't been declared yet).

## History tracking

State observations are time-stamped like any other Observation (`effective_datetime`). Two analytics helpers expose the chronological sequence:

- **`get_biomarker_state_history(tenant_id, patient_id, slug)`** — chronological list of `{timestamp, state_code, state_system, display, is_normal, observation_id}`. Powers a step/stair timeline of categorical results.
- **`get_multi_state_history(tenant_id, patient_id, slug)`** — `{component_code: [...]}` mapping, one timeline per sub-context. Powers a multi-track swimlane for panels.

The status badge for a state observation (`_get_observation_status` in `analytics_service.py`) returns `"Normal"` when the value's coding is in the biomarker's normal set, `"Abnormal"` otherwise. For multi-state panels, the badge is `"Abnormal"` if **any** component is non-normal — the conservative choice for a single-badge summary.

## FHIR R4 facade

State biomarkers work end-to-end through the FHIR R4 facade at `/api/v1/fhir/R4/Observation`:

| Search param | Behaviour |
|---|---|
| `value-concept` | Token search on `valueCodeableConcept.coding[].code`. Honors `system|code` narrowing. Also matches `component[].valueCodeableConcept` so multi-state panels surface the same way as single-state observations. |
| `value-string` | Case-insensitive substring on `value_string` (free-text results). |
| `component-code` | Narrows multi-state panels by sub-context code (e.g. organism in a microbiology panel). |

`GET /fhir/R4/Observation?value-concept=POS&patient=<id>` returns every Positive observation for a patient regardless of which STATE biomarker produced it.

## AI extraction

The OCR/NLP pipeline extracts state values via **sibling fields**, not by loosening the numeric `value` field:

- `KnownBiomarkerExtract.value_state_code` + `value_state_system` + `value_state_display` carry the categorical result.
- `value` remains `Optional[float]` (was required). A Pydantic validator enforces exactly-one-of (`value`, `value_state_code`) — preventing the footgun where loosening `value: float` to `Union[float, str]` would silently turn on every downstream numeric code path.
- The Pass 1 prompt is given the biomarker's `allowed_states` list and picks a code from it; an unknown code falls back to `unknown` (DataAbsentReason) rather than guessing.

STATE biomarkers are an explicit admin decision on the definition; unknown biomarkers extracted from documents default to `value_type=quantity` (auto-typing is out of scope).

## Telemetry exclusion

STATE biomarkers are hard-blocked from telemetry at three layers:

1. **DB CHECK constraint** on `biomarker_definitions` rejects `value_type='state' AND is_telemetry=TRUE`.
2. **Endpoint guard** on `PATCH /biomarkers/{id}` returns HTTP 400 if you try to set `is_telemetry=true` on a STATE biomarker.
3. **Migration task backstop** — `migrate_biomarker_data` early-returns with `status=failed` if invoked on a STATE biomarker, even though layers 1 and 2 should prevent the task from ever being queued.

The telemetry hypertable (`telemetry_data.value`) is `Float NOT NULL` — categorical values have nowhere to go. Numeric-only continuous aggregates (`AVG`/`MIN`/`MAX`/`SUM` per bucket) are correct as-is.

## SDK / integration authoring

The Integrations SDK `ObservationBuilder` exposes the proper categorical setter for STATE biomarkers:

```python
ObservationBuilder(tenant_id, patient_id)
    .set_biomarker("94500-6", "SARS-CoV-2 PCR")
    .set_value_codeable_concept(
        code="POS",
        system="http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
        display="Positive",
    )
    .set_effective_date(timestamp)
    .build()
```

`set_value_string` is reserved for genuinely free-text results (no controlled vocabulary). The validator rejects `value_string` on STATE biomarkers — use `set_value_codeable_concept` for any coded categorical value.

## See also

- [Architecture](ARCHITECTURE.md) — the Biomarker Engine section.
- [Ontology & Catalog](ONTOLOGY_CATALOG.md) — the JSON catalog format (gains optional `value_type` + `allowed_states` per biomarker).
- [Telemetry & TimescaleDB](TELEMETRY_AND_AGGREGATION.md) — why STATE biomarkers are excluded from telemetry.
- [FHIR R4 Facade](FHIR_R4_FACADE.md) — search params and the external API surface.
- `dev/plans/state-biomarkers-2026-08-05.md` — the implementation plan.

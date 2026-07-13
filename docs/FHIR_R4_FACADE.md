---
title: "FHIR R4 Facade — Health Assistant"
description: "The FHIR R4 REST API facade of Health Assistant, a self-hosted health records platform. 18 conformance resources, CapabilityStatement, search Bundles, Provenance-on-write, and OperationOutcome errors."
---

# FHIR R4 Facade — Developer Guide

Health Assistant exposes a **conformant FHIR R4 REST API** at `/api/v1/fhir/R4/*`.
This is the **interop surface** for external systems — not the frontend's API.
The frontend uses domain endpoints (`/patients/*`, `/observations/*`,
`/examinations/*`, `/medications/*`, etc.) which return ORM-shape dicts with
app-specific fields the UI needs. This document explains how to use the facade
and how to add new resources.

> **Coverage**: 18 FHIR resources registered; standard search params + Bundle
> responses + canonical CRUD + soft-delete tombstones + Provenance-on-write.
> Includes two **computed** terminology resources (`CodeSystem`/`ValueSet`) that
> project from the `concepts` table without a dedicated backing table.
> Advanced conformance (`POST /_search`, `_format=xml`, transaction/batch
> Bundle) is deferred.

---

## Architecture in one paragraph

The facade is a **thin HTTP layer** over the existing ORM models. Each model
already has a `to_fhir_dict()` method (validated by `fhir.resources`). The
facade parses incoming canonical FHIR JSON via `fhir_to_orm()` converters,
persists via SQLAlchemy, and serializes the response via `to_fhir_dict()`.
A central `RESOURCE_REGISTRY` drives route registration, CapabilityStatement
generation, and search dispatch — **adding a new resource is ~5 lines of registry code**.

This is a **hybrid storage** architecture: no dual-write. Existing tables became
FHIR-canonical via `to_fhir_dict()` projections. Only three new tables were
created (`fhir_provenance`, `fhir_devices`, `fhir_communications`) for concepts
with no app-table analog.

---

## HTTP surface

All routes require Bearer JWT auth via `get_current_user_with_tenant_override`
(two modes: platform user JWT from `/auth/login`, or service-account JWT from
`/auth/service-account`), **except** `GET /metadata` (no auth per FHIR spec).

SYSTEM_ADMIN can override the tenant scope via the `X-Tenant: <uuid>` header.
Service accounts are bound to their JWT's tenant and cannot override.

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/fhir/R4/metadata` | CapabilityStatement (dynamic, 5-min Cache-Control). |
| `GET` | `/fhir/R4/{Resource}` | Search → Bundle (`type=searchset`) with `total` + `link[]`. |
| `GET` | `/fhir/R4/{Resource}/{id}` | Read one → canonical FHIR JSON + `ETag`/`Last-Modified`. Returns `410 Gone` if soft-deleted. |
| `POST` | `/fhir/R4/{Resource}` | Create → `201 Created` + `Location` + canonical body. Records a `Provenance`. |
| `PUT` | `/fhir/R4/{Resource}/{id}` | Update → 200 + canonical body (bumps `VersionedMixin.version`). |
| `DELETE` | `/fhir/R4/{Resource}/{id}` | Soft-delete (`deleted_at = now()`) → `204 No Content`. Records a final `Provenance`. |

**Standard search params** (every resource):
- `_id` (token, repeatable)
- `_lastUpdated` (date with FHIR prefixes: `gt`, `ge`, `lt`, `le`, `eq`, `sa`, `eb`, `ap`)
- `_count` (integer; default 50, max 250)
- `_sort` (comma-separated; `-` prefix = descending; allowlist per resource)
- `_format` (`json` default; `xml` is Phase 8)

**Resource-specific params**: `patient`/`subject`, `encounter`, `code`, `status`,
`category`, `date`, `onset-date`, `effective`, `intent`, etc. See
`app/facade/search_params.py:RESOURCE_PARAMS` for the full allowlist per resource.

**Errors**: every error response is a FHIR `OperationOutcome`:

```json
{
  "resourceType": "OperationOutcome",
  "issue": [
    {
      "severity": "error",
      "code": "not-found",
      "diagnostics": "Patient/abc not found"
    }
  ]
}
```

Status codes: `400` (invalid FHIR), `404` (not found / unknown resource type),
`405` (interaction not supported — e.g. DELETE on immutable Provenance),
`410` (tombstone), `500` (unexpected; includes correlation id).

---

## Registered resources (18)

| Resource | Backed by | Notes |
|----------|-----------|-------|
| `Patient` | `fhir_patients` | |
| `Observation` | `fhir_observations` | |
| `Condition` | `clinical_events` | Projected via `ClinicalEvent.to_fhir_dict()`. Metadata-driven JSONB stays untouched. |
| `Encounter` | `examinations` | Projected via `ExaminationModel.to_fhir_dict()`. Default status `finished`, class `AMB`. |
| `AllergyIntolerance` | `fhir_allergy_intolerances` | |
| `MedicationStatement` | `fhir_medications` | Filter: `intent = statement` (default) |
| `MedicationRequest` | `fhir_medications` | Filter: `intent != statement` (order/plan/proposal) |
| `Medication` | `medication_catalog` | Drug definitions. Read-only via facade. |
| `Immunization` | `patient_immunizations` | Patient dose records. Read + search-type only (REST CRUD at `/vaccines/*`). `date`→`administered_at`, `vaccine-code`→JSONB text. |
| `DiagnosticReport` | `fhir_diagnostic_reports` | |
| `DocumentReference` | `documents` | Attachment is metadata-only (`urn:ha-document:<id>`); binary resolves via the existing download endpoint, not the facade. |
| `Device` | `fhir_devices` (new) | Backfilled from `user_integrations`. |
| `Communication` | `fhir_communications` (new) | Clinical messaging — distinct from push notifications. |
| `Organization` | `fhir_organizations` | Now mixes in `TimestampMixin` (audit D15 — gained `created_at`/`updated_at` + indexes) and `SoftDeleteMixin` (audit D6). |
| `Practitioner` | `doctors` (DoctorModel) | |
| `Provenance` | `fhir_provenance` (new) | Immutable; create + read + search only (no update/delete). |
| `CodeSystem` | `concepts` (computed) | **Computed resource** — no dedicated table. Projects disease-kind concepts (`kind=disease`, ICD-10) as a FHIR `CodeSystem` (`id=ha-diseases`). Read + search-type only; uses `read_fn`/`search_fn` hooks (`app/facade/terminology.py`) that bypass the generic table-query dispatcher. |
| `ValueSet` | `concepts` (computed) | **Computed resource** — publishes the disease concepts as a `ValueSet` (`id=ha-diseases`, compose includes the ICD-10 system). Read + search-type only. |

---

## How to add a new FHIR resource to the facade

Two cases:

### Case A — your model already exists and has a `to_fhir_dict()`

1. **Add a reverse converter** in `backend/app/services/fhir_converter.py`:
   ```python
   def fhir_to_my_resource_orm(f: Dict[str, Any]) -> Dict[str, Any]:
       return _clean({
           "id": f.get("id"),
           # ... map FHIR camelCase → ORM snake_case
       })

   _TO_ORM = {
       ...
       "MyResource": fhir_to_my_resource_orm,
   }
   ```

2. **Register** in `backend/app/facade/registry.py`'s `register_all()`:
   ```python
   RESOURCE_REGISTRY.register(
       ResourceEntry(
           resource_type="MyResource",
           model=MyModel,
           fhir_to_orm_fn=fhir_to_my_resource_orm,
       )
   )
   ```

3. **Add search params** (optional) in `backend/app/facade/search_params.py`:
   - `RESOURCE_PARAMS["MyResource"] = frozenset({"patient", "code", ...})`
   - `SORT_COLUMNS["MyResource"] = {"_id": "id", "_lastUpdated": "updated_at", ...}`

4. **Add a soft-delete column** if not present (for `DELETE` to work):
   - Migration: `ALTER TABLE my_table ADD COLUMN deleted_at TIMESTAMPTZ NULL`
   - Mixin: add `SoftDeleteMixin` to the model class declaration.

5. **Tests**: add `tests/test_fhir_r4_my_resource.py` covering forward projection,
   reverse converter, round-trip, and validation rejection.

That's it. The CapabilityStatement, the HTTP routes, the search dispatcher,
and the Provenance-on-write hook all pick up the new resource automatically.

### Case B — your model needs a new table (no app analog)

1. **Create the model** in `backend/app/models/fhir/my_resource.py` with
   `Base, UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin` and a
   `to_fhir_dict()` method that uses `build_fhir_resource`.
2. **Migration** to create the table.
3. **Register** the model in `backend/app/models/__init__.py` (so Alembic sees it).
4. **Reverse converter** + **registry entry** (same as Case A).
5. **Tests**.

### Case C — computed resource (no backing table — e.g. CodeSystem/ValueSet)

Terminology resources like `CodeSystem` and `ValueSet` don't map 1:1 to a single
table — they aggregate rows from the `concepts` table into one FHIR resource.
The facade supports this via optional `read_fn`/`search_fn` hooks on
`ResourceEntry` that bypass the generic table-query dispatcher:

1. **Build the projection** in `backend/app/facade/terminology.py` — an async
   function that queries the source table, constructs the FHIR-shaped dict, and
   validates it via `build_fhir_resource`.
2. **Register** with `read_fn`/`search_fn` set (no `fhir_to_orm_fn` — writes are
   not supported):
   ```python
   RESOURCE_REGISTRY.register(ResourceEntry(
       resource_type="CodeSystem",
       model=Concept,  # projection source (not queried generically)
       interactions=frozenset({"read", "search-type"}),
       read_fn=make_read_fn("CodeSystem"),
       search_fn=make_search_fn("CodeSystem"),
       tenant_scope="none",
       versioned=False,
       soft_delete=False,
   ))
   ```
3. The generic `crud.read`/`crud.search` check for these hooks first and
   delegate when present; otherwise the normal table-query path runs.
4. **Tests**: `tests/test_codesystem_facade.py` covers read/search/404 +
   CapabilityStatement advertising + write-not-supported (405).

---

## Architecture decisions

### Why hybrid storage (not one-table-per-resource)

Every FHIR server project faces this choice. The HAPI/JPA pattern (one dedicated
table per FHIR resource) is spec-pure but introduces dual-write for any resource
with an app-concept analog (Condition ↔ Clinical Event, Encounter ↔ Examination,
DocumentReference ↔ Document). Health Assistant chose the **hybrid** model:
existing tables became FHIR-canonical via `to_fhir_dict()`, no new tables for
resources with an analog. Three new tables were justified only where no app-table
exists (`Provenance`, `Device`, `Communication`).

This is also how the existing FHIR-shaped JSONB columns already worked (Patient,
Observation, MedicationStatement, AllergyIntolerance, Organization,
DiagnosticReport are all "FHIR-enhanced relational rows"). The facade just exposes
that investment at the HTTP layer.

### Why a separate facade (not retrofit the domain endpoints)

The domain endpoints (`/patients/*`, `/observations/*`, ...) speak **ORM-shape**
(snake_case + app fields like `tenant_id`, `biomarker_id`, `normalized_value`).
The frontend depends on that contract. Retrofitting them to canonical FHIR would
break the frontend and fight FHIR's data model. The facade at `/api/v1/fhir/R4/*`
speaks **canonical FHIR** (camelCase, validated by `fhir.resources`) and is
intended for external systems. This is the standard healthcare-IT pattern: an
internal app API for the UI + a FHIR interop surface for external clients
(Cerner/Epic run the same way).

### Why soft-delete (not hard-delete)

FHIR spec says deleted resources should return `410 Gone` (tombstone), not
`404 Not Found` — so callers can distinguish "never existed" from "was deleted".
Hard deletes lose that signal. The `SoftDeleteMixin.deleted_at` column is
nullable; reads check `deleted_at IS NULL`; facade DELETE sets it to `now()`.
Subsequent reads return `410` with an OperationOutcome.

### Why Provenance-on-write is best-effort

Provenance is immutable and append-only — never blocks the parent write.
If the Provenance insert fails (e.g. validation issue, DB hiccup), the facade
logs a warning and continues. The clinical write succeeds; only the audit trail
has a gap. This matches common FHIR server behavior.

---

## What the frontend uses instead

The frontend does **not** talk to `/fhir/R4/*`. It uses the domain endpoints,
which return ORM-shape dicts (snake_case + app fields):

- `/patients/*` for patient identity CRUD
- `/observations/*` for biomarker readings CRUD
- `/examinations/*` for the clinical-visit workflow (AI extraction, documents, doctors)
- `/medications/*` for prescriptions + catalog
- `/allergies/*` for allergy intolerance + catalog
- `/clinical-events/*` for the metadata-driven events system
- `/biomarkers/*` for the biomarker definition catalog
- `/documents/*` for file storage + OCR pipeline
- `/analytics/*` for trend/dashboard aggregation
- `/telemetry/*` for TimescaleDB hypertable reads

## File map

```
backend/app/
├── facade/                                 # the facade package (outside api/v1 — no circular imports)
│   ├── __init__.py                         # docstring
│   ├── registry.py                         # RESOURCE_REGISTRY + register_all() (18 resources)
│   ├── terminology.py                      # computed CodeSystem/ValueSet projections (Phase 6)
│   ├── search_params.py                    # FhirSearchParams + parse_search_params + RESOURCE_PARAMS + SORT_COLUMNS
│   ├── responses.py                        # OperationOutcome + 201/200/204/410/404 helpers
│   ├── bundle.py                           # build_search_bundle (searchset Bundle + pagination links)
│   └── crud.py                             # generic search/read/create/update/delete dispatcher
├── api/v1/endpoints/
│   ├── fhir_r4.py                          # the 6 HTTP routes (GET/POST/PUT/DELETE /fhir/R4/...)
│   ├── patients.py                         # domain endpoint — Patient CRUD (ORM-shape, frontend-facing)
│   └── observations.py                     # domain endpoint — Observation CRUD (ORM-shape, frontend-facing)
├── models/
│   ├── base.py                             # SoftDeleteMixin (deleted_at)
│   ├── clinical_event.py                   # to_fhir_dict() → Condition
│   ├── examination_model.py                # to_fhir_dict() → Encounter
│   ├── document_model.py                   # to_fhir_dict() → DocumentReference
│   ├── fhir/
│   │   ├── medication.py                   # intent discriminator; MedicationCatalog.to_fhir_dict() → Medication
│   │   ├── allergy.py                      # to_fhir_dict() (already existed; now gated by assert_valid_fhir)
│   │   ├── provenance.py                   # new — ProvenanceModel
│   │   ├── device.py                       # new — DeviceModel
│   │   └── communication.py                # new — CommunicationModel
│   └── enums.py                            # MedicationIntent enum (statement|order|plan|proposal)
└── services/
    ├── fhir_facade_service.py              # CapabilityStatement builder + get_software_version()
    ├── fhir_converter.py                   # fhir_to_*_orm() reverse converters + _TO_ORM dispatch table
    ├── fhir_helpers.py                     # build_fhir_resource, parse_fhir_resource, assert_valid_fhir, build_meta
    └── provenance_service.py               # record_provenance() best-effort hook

backend/alembic/versions/                   # 4 new migrations
├── a7484842ecd4_add_deleted_at_to_fhir_tables.py
├── 0ecc9ad85909_add_intent_to_fhir_medications.py
├── c987390e2778_create_fhir_provenance.py
└── 34414d55a822_create_devices_and_communications.py

backend/tests/                              # 131 new tests across 6 files
├── test_fhir_r4_phase1.py                  # scaffolding (38 tests)
├── test_fhir_r4_condition.py               # C7 + C16 (21 tests)
├── test_fhir_r4_encounter.py               # C8 (21 tests)
├── test_fhir_r4_document_reference.py      # C14 (19 tests)
├── test_fhir_r4_medication.py              # C11 + C12 (23 tests)
├── test_fhir_r4_allergy_gate.py            # C13 (5 tests)
├── test_fhir_r4_provenance.py              # C10 model + service (13 tests)
├── test_fhir_r4_device_communication.py    # C9 + C15 (18 tests)
└── test_fhir_r4_facade_http.py             # HTTP layer wiring (17 tests)
```

---

## What's deferred

| Item | Where | Reason |
|------|-------|--------|
| `POST /fhir/R4/{Resource}/_search` | Phase 8 | Useful for long query strings; no users asking yet. |
| `_format=xml` | Phase 8 | Adds `lxml` dependency; JSON is sufficient for interop. |
| Transaction/batch Bundle (`POST /fhir/R4` with `type=transaction\|batch`) | Phase 8 | Complex; low priority, deferred. Note: the **import path** (`/import/*`, see `docs/EXPORT_IMPORT.md` §6) already honors `entry.request.method` (`PUT`/`POST`/`DELETE`/`ifNoneExist`) for round-tripping third-party transaction Bundles — this deferred item is specifically the facade's *system-level* `POST /fhir/R4` endpoint (audit G4), a separate surface. |
| SMART-on-FHIR scopes (`/.well-known/smart-configuration`, `patient/*.read`) | Stage 4 | Facade uses existing JWT auth + tenant scoping for now. |
| US Core profile validation | Stage 4 | Profiles are an internal-best-practice, not required for interop. |
| `_include` / `_revinclude` chained search params | Phase 8 | Default to depth=1; no recursion for v1. |
| FHIR compartments (formal) | Stage 4 | Tenant isolation already provides informal compartments. |

---

## References

- **Architecture**: `docs/ARCHITECTURE.md` "FHIR R4 Facade (Stage 3)".
- **Status**: `docs/STATUS.md` (FHIR R4 facade bullet under Backend completed).
- **Skills**: `clinical-data` §1, `backend` §10.
- **FHIR R4 spec**: <https://hl7.org/fhir/R4/>

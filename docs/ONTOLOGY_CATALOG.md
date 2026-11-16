# Clinical Ontology Catalog

The Health Assistant platform uses a dynamic, JSON-based clinical ontology. This allows system administrators to bulk-import standard medical definitions (like LOINC biomarkers and standard units) without modifying the core application code.

Catalogs can be imported via the **System Administration -> Clinical Ontology** UI by providing a URL to a JSON file (e.g., a raw GitHub URL) or uploading a `.json` file directly.

> Biomarker definitions, medication, allergy, vaccine, anatomy and concept catalogs are unified under a declarative `CatalogRegistry` (`app/catalogs/`) with one CRUD/scope-tier-access/search/FHIR/edge contract, reachable via the `/catalogs/{type}` meta-layer, unified search (`GET /catalogs/search`), and the cross-domain knowledge graph (`GET /catalogs/{type}/{id}/relations`). See [ARCHITECTURE.md § Catalog Registry](ARCHITECTURE.md#catalog-registry--cross-domain-knowledge-graph-appcatalogs) and [API.md § Catalogs & Search](API.md). The import flow below remains the primary path for bulk-loading LOINC biomarker definitions.

## One write contract per catalog (writes)

Every catalog type has exactly **one** write path: typed Pydantic create/update
schemas (``extra="forbid"``) + one service function per operation in
`app/services/catalog/catalog_write_service.py`. Both entry points dispatch to
it — the generic `POST/PUT/DELETE /catalogs/{type}` routes (validate the raw
body against the descriptor's schema, then call the service via the adapter)
and the domain routes (`POST /medications/catalog`, `POST /biomarkers/`,
`POST /concepts`, `POST /anatomy`, ...), which are thin: validate → call the
same service → response model. No field is ever silently dropped.

Contract rules:

- **Unknown / system-owned fields are rejected with `422`**, not ignored:
  `scope`, `tenant_id`, `created_by`, `seed_key`, `id` and timestamps are not
  on any write schema. `seed_key` (the immutable natural key) is
  response-only — it is written by seeds/imports, never by API clients.
- **`coding_system` + `code` are writable** on medication/allergy (and carried
  by biomarker/vaccine/concept where the columns exist).
- **Scope is derived from the caller's role** (SYSTEM_ADMIN→system,
  ADMIN/MANAGER→tenant, USER→user); concepts follow `ConceptService`'s
  curated-taxonomy RBAC (USER is read-only, globals are SYSTEM_ADMIN-only).
- **Conflicts are typed `409`s** (`{"code": "catalog_conflict", "field": ...}`):
  duplicate `(coding_system, code)` (medication/allergy, per tenant scope),
  duplicate derived slug (vaccine, per tenant scope), duplicate concept slug.
  Services translate `IntegrityError` after rollback — a constraint violation
  never surfaces as a 500.
- **Server-side derivations**: the vaccine `slug` is derived from the name
  (clients never send it — a `slug` key → 422); the biomarker `slug` may be
  omitted — the service derives it from the name the same way (deterministic,
  unique per tenant scope via an auto-suffix) and an explicit slug stays
  honored (duplicate → 409), so prefilled dialogs never send one; the
  biomarker `preferred_unit_symbol` → unit id (unknown symbol → 400), and
  `class_concept_slug` → tenant-scoped `biomarker_class` concept (an
  explicit `class_concept_id` wins, an unresolvable slug is a 400); anatomy
  `class_concept_slug` → `anatomy_class` concept (system + caller's tenant
  only, so one tenant can never shadow another's resolution); reference
  ranges and `allowed_states` persist on create.
- **Ownership has exactly one representation**: `scope`
  (`system`/`tenant`/`user`) + `tenant_id` (+ `created_by` for user rows).
  The former `is_custom` boolean was a derived duplicate and is gone from
  every catalog response and filter (the Catalogs UI's "Custom only" toggle
  reads `scope`).
- **Vaccine → disease links are graph rows, not a field**: `vaccines.json`'s
  readable `target_diseases` refs materialize as approved `PREVENTS`
  `concept_edges` rows (migration `0012` dropped the JSONB column); edits go
  through the graph-links API (`POST /concept-edges`,
  `edge_endpoint_type=immunization`), never a vaccine write field.
- **Every write is audited** (`catalog_audit_log`), whichever door it entered;
  the full serialized row — the same keys the read endpoints return, including
  computed fields (`class_concept_slug`, `preferred_unit_symbol`,
  `allowed_states`, codes, `seed_key`) — comes back from create, update and
  promote.

## One manual definition surface (the browse dialog)

Users create catalog definitions from the **catalog browse dialog**, not from
the record forms: every catalog-backed picker (`CatalogItemPicker`) can opt
into a **New \<type\>** action in its browse list header (single-type fields,
single-select mode, list view). It opens one shared create dialog per type —
medication, allergy, vaccine and biomarker — built on the same registry forms
the admin workspace uses, and writes through the generic `POST /catalogs/{type}`
contract above. The dialog never sends `slug`/`seed_key` (server-owned
identity — the backend derives the biomarker slug from the name when the
payload omits it, and the form renders the slug field disabled so an edit
that would be silently dropped can never happen); after the create it
reconciles biomarker stratified reference
ranges (`POST /biomarkers/{id}/reference-ranges/...` sync) and persists graph
links (`POST /concept-edges` batch, `src_type` from the type descriptor's
`edge_endpoint_type`), then selects the new item in the picker.

Record forms (prescriptions, allergies, vaccinations, biomarker readings)
only **pick existing** catalog items — there is no inline define-new panel and
no catalog row is created on record submit. The created definition is
reusable and visible to the whole health record (tenant), even when scoped to
its creator (`scope=user` rows stay tenant-visible). Medication definitions
offer an AI assist (`define_medication`) that drafts
description/indications/dosage/contraindications/side effects for review;
there is deliberately no such assist for allergy or biomarker.

## JSON Schema Structure

The root of the JSON file must contain three main arrays/objects: `metadata`, `units`, and `biomarkers`.

### Example Payload

```json
{
  "metadata": {
    "version": "1.0.0",
    "source": "Health-Assistant Community Catalog",
    "last_updated": "2026-06-11"
  },
  "units": [
    { 
      "symbol": "mg/dL", 
      "name": "Milligrams per deciliter", 
      "quantity_type": "MASS_CONCENTRATION" 
    }
  ],
  "biomarkers": [
    {
      "slug": "glucose-fasting",
      "coding_system": "loinc",
      "code": "2345-7",
      "name": "Fasting Glucose",
      "class_concept_slug": "blood-laboratory",
      "preferred_unit_symbol": "mg/dL",
      "aliases": ["FBS", "Fasting Blood Sugar"],
      "reference_range_min": 70,
      "reference_range_max": 99,
      "info": "Measures blood sugar levels after fasting. Critical for diabetes screening."
    }
  ]
}
```

## Field Definitions

### 1. `metadata` (Object)
Informational data about the catalog being imported.
- **`version`** (String): Version of the catalog.
- **`source`** (String): The author or organization providing the catalog.
- **`last_updated`** (String): Date string (e.g., YYYY-MM-DD).

### 2. `units` (Array of Objects)
Defines the standard units of measurement. The system uses these to normalize data.
- **`symbol`** (String, Required): The exact text representation of the unit (e.g., "mg/dL", "%", "bpm"). Must be unique.
- **`name`** (String, Required): Human-readable name.
- **`quantity_type`** (String, Optional): Categorization for conversion engines. Must match one of the system Enums:
  - `MASS_CONCENTRATION`
  - `MOLAR_CONCENTRATION`
  - `NUMBER_CONCENTRATION`
  - `PERCENTAGE`
  - `PRESSURE`
  - `VOLUME`
  - `MASS`
  - `TIME`
  - `RATIO`
  - `TEMPERATURE`
  - `OTHER` (Default)

### 3. `biomarkers` (Array of Objects)
Defines the medical tests, vital signs, and measurements.
- **`slug`** (String, Required): A unique, URL-friendly, kebab-case identifier used internally by the database (e.g., `heart-rate`).
- **`coding_system`** (String, Optional): The official medical coding system this biomarker belongs to. Defaults to `loinc`. Must match the `CodingSystem` Enum:
  - `loinc` (Logical Observation Identifiers Names and Codes)
  - `snomed` (SNOMED Clinical Terms)
  - `custom` (For proprietary hospital codes or unstandardized metrics)
- **`code`** (String, Optional): The exact identifier from the `coding_system` (e.g., `"2345-7"` for LOINC Glucose).
- **`name`** (String, Required): The clean, display name of the biomarker.
- **`class_concept_slug`** (String, Optional): The biomarker-class **concept** slug (e.g., `blood-laboratory`, `vital-signs`), resolved to `biomarker_definitions.class_concept_id` (see [TAXONOMY.md](TAXONOMY.md)). It is the only class key the import accepts — there is no free-text `category` column and no name translation; an explicit `class_concept_id` wins over the slug.
- **`preferred_unit_symbol`** (String, Optional): The `symbol` of the unit defined in the `units` array that this biomarker should default to for rendering charts.
- **`aliases`** (Array of Strings, Optional): Synonyms or common abbreviations used by the AI to match OCR data to this definition (e.g., `["WBC", "White Count"]`).
- **`reference_range_min`** (Float, Optional): The standard clinical lower bound. QUANTITY biomarkers only — STATE biomarkers use `allowed_states` with `is_normal` flags instead.
- **`reference_range_max`** (Float, Optional): The standard clinical upper bound. QUANTITY biomarkers only.
- **`info`** (String, Optional): Markdown-formatted text explaining the clinical significance of the biomarker to the patient.
- **`value_type`** (String, Optional): Discriminator — `quantity` (default, numeric) or `state` (categorical, drawn from a controlled vocabulary). STATE biomarkers carry no `preferred_unit_symbol` / `reference_range_min` / `reference_range_max`; instead they declare `allowed_states`. See [STATE_BIOMARKERS.md](STATE_BIOMARKERS.md).
- **`supports_multi_state`** (Boolean, Optional, default false): STATE biomarkers only. When true, the biomarker accepts `Observation.component[]` (one `valueCodeableConcept` per sub-context, e.g. one organism per row in a microbiology panel) instead of a single top-level value.
- **`allowed_states`** (Array of Objects, Optional): STATE biomarkers only. Each entry is `{state_slug: <biomarker_states.slug>, is_normal: <bool>, sort_order: <int>}`. Required non-empty for STATE biomarkers. The slugs resolve to the universal `biomarker_states` catalog at import time; an unknown slug fails the import.

## Import Behavior

When an import is triggered:
1. **Upsert Logic:** The system uses the `slug` (for biomarkers) and `symbol` (for units) as the primary keys. If a record with that key already exists, it will **update** it. If it does not exist, it will **insert** it.
2. **Safety:** Importing a catalog will *never* delete existing records or patient data. It only adds or updates definitions.
3. **Background Execution:** Because catalogs can contain thousands of records, the import runs as an asynchronous background task. Progress can be monitored in the Task Logs.

## Distributing Catalogs (LOINC Licensing)

If you create a public GitHub repository to share a catalog containing LOINC codes, you **must** include the LOINC attribution per their licensing requirements:

> This material contains content from LOINC (https://loinc.org). LOINC is copyright © Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and Codes (LOINC) Committee and is available at no cost under the license at https://loinc.org/license. LOINC® is a registered United States trademark of Regenstrief Institute, Inc.
## Data Mapping & Normalization Logic

When external data enters the system (via OCR Document Extraction, API integrations, or Webhooks), it must be assigned to a unified `BiomarkerDefinition`. The system uses a strict waterfall logic to establish this `biomarker_id` link:

1. **Primary Match (By Code):** If the incoming payload provides a standardized medical code (e.g., LOINC), the system queries for `BiomarkerDefinition.code == incoming_code`.
2. **Fallback 1 (By Name):** If no code exists, the system performs a case-insensitive search against the biomarker's display name (`BiomarkerDefinition.name.ilike(incoming_text)`).
3. **Fallback 2 (By Slug/Aliases):** If the name fails, it slugifies the incoming text (e.g., "Heart Rate" -> "heart-rate") and attempts a match against `BiomarkerDefinition.slug`. (Aliases are also checked in analytical queries).
4. **Auto-Creation (Safety Net):** If no matches are found, the system will dynamically insert a new custom `BiomarkerDefinition` into the catalog using the provided name and a generated slug, ensuring no clinical data is ever dropped due to a catalog miss.

### Retroactive Remapping

When observations exist without a `biomarker_id` link (e.g., imported before a definition existed), they appear in biomarker lists with raw, unformatted names and a ⚡ "unmapped" indicator. Users can resolve these inline via a popup that offers:
- **Create biomarker**: opens the shared definition dialog prefilled with the raw name and auto-relinks after the create.
- **Map to existing**: searches the catalog and relinks to a chosen definition.

Both call `POST /api/v1/biomarkers/{biomarker_id}/remap` with a `source_name` (matched against `code.text`) and optional `patient_id` scope. On the analytics side, the trends query (`GET /analytics/trends`) also expands `biomarker_codes` filters against definition names/aliases so unmapped observations appear in the detail page even before remapping.

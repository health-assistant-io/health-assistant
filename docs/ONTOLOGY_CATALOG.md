# Clinical Ontology Catalog Format

The Health Assistant platform uses a dynamic, JSON-based clinical ontology. This allows system administrators to bulk-import standard medical definitions (like LOINC biomarkers and standard units) without modifying the core application code.

Catalogs can be imported via the **System Administration -> Clinical Ontology** UI by providing a URL to a JSON file (e.g., a raw GitHub URL) or uploading a `.json` file directly.

> **Catalog Registry (Phase 0–5 + access-control/audit/UI rework):** biomarker definitions, medication catalog, allergy catalog, and vaccine catalog are now unified under a declarative `CatalogRegistry` (`app/catalogs/`) with one CRUD/scope-tier-access/search/FHIR/edge contract. They're accessible via the `/catalogs/{type}` meta-layer and appear in unified search (`GET /catalogs/search`) and the cross-domain knowledge graph (`GET /catalogs/{type}/{id}/relations`). Access is ownership-based via scope tiers (`system`/`tenant`/`user`); every write is audit-logged (`GET /catalogs/{type}/{id}/history`). See [ARCHITECTURE.md § Catalog Registry](ARCHITECTURE.md#catalog-registry--cross-domain-knowledge-graph-appcatalogs) and [API.md § Catalogs & Search](API.md). The import flow below remains the primary path for bulk-loading LOINC biomarker definitions.

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
      "category": "blood_laboratory",
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
- **`category`** (String, Optional): The clinical grouping (e.g., `blood_laboratory`, `vital_signs`, `ophthalmology`). On import this is translated — via `biomarker_category_to_concept_slug` — to a `biomarker_class` **concept** stored on `biomarker_definitions.class_concept_id` (see [TAXONOMY.md](TAXONOMY.md)); it is not stored as a free-text column.
- **`preferred_unit_symbol`** (String, Optional): The `symbol` of the unit defined in the `units` array that this biomarker should default to for rendering charts.
- **`aliases`** (Array of Strings, Optional): Synonyms or common abbreviations used by the AI to match OCR data to this definition (e.g., `["WBC", "White Count"]`).
- **`reference_range_min`** (Float, Optional): The standard clinical lower bound.
- **`reference_range_max`** (Float, Optional): The standard clinical upper bound.
- **`info`** (String, Optional): Markdown-formatted text explaining the clinical significance of the biomarker to the patient.

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
- **Create biomarker**: creates a new definition from the raw name and auto-relinks.
- **Map to existing**: searches the catalog and relinks to a chosen definition.

Both call `POST /api/v1/biomarkers/{biomarker_id}/remap` with a `source_name` (matched against `code.text`) and optional `patient_id` scope. On the analytics side, the trends query (`GET /analytics/trends`) also expands `biomarker_codes` filters against definition names/aliases so unmapped observations appear in the detail page even before remapping.

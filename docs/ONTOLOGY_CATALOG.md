# Clinical Ontology Catalog Format

The Health Assistant platform uses a dynamic, JSON-based clinical ontology. This allows system administrators to bulk-import standard medical definitions (like LOINC biomarkers and standard units) without modifying the core application code.

Catalogs can be imported via the **System Administration -> Clinical Ontology** UI by providing a URL to a JSON file (e.g., a raw GitHub URL) or uploading a `.json` file directly.

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
- **`category`** (String, Optional): The clinical grouping used for UI filtering (e.g., `blood_laboratory`, `vital_signs`, `ophthalmology`).
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

> This material contains content from LOINC (http://loinc.org). LOINC is copyright © Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and Codes (LOINC) Committee and is available at no cost under the license at http://loinc.org/license. LOINC® is a registered United States trademark of Regenstrief Institute, Inc.
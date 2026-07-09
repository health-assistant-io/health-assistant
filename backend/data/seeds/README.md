# Seed Files

Health Assistant's reference/catalog data ships as JSON seed files in this
directory. They are loaded on every application startup by
`SeedService.seed_all()` (`backend/app/services/seed_service.py`) — a single
ordered, idempotent pipeline. Re-running a stage reconciles existing rows to
the JSON (upsert by slug / natural key), so editing a file and restarting is
the normal way to evolve catalog data.

## Envelope

Every file (except `default_catalog.json`) uses the standard envelope:

```json
{ "metadata": { "version": "1.0.0", "source": "...", "last_updated": "YYYY-MM-DD", "count": N },
  "items": [ ... ] }
```

`default_catalog.json` uses `{ metadata, units[], biomarkers[] }`.

## Identity model

**Slug is the stable join key; `name` is mutable display.** Cross-references
are by slug (`parent_slug`, `src_slug`/`dst_slug`, `panel_slug`,
`class_concept_slug`). `coding_system` + `code` are carried as identity
*evidence* (FHIR-aligned) but are not the join key. See
[docs/SEEDING_AND_DEMOS.md §7](../../../docs/SEEDING_AND_DEMOS.md#7-exporting-an-instance-back-to-seeds).

## Field schemas

Required fields are marked **(req)**. `?` = optional.

### `concepts.json` — unified taxonomy nodes
- `slug` **(req)**, `name` **(req)**
- `kinds` **(req)** — list of `ConceptKind` values (`specialty`,
  `examination_category`, `biomarker_class`, `biomarker_panel`,
  `anatomy_class`, `event_category`, `document_category`, `body_system`,
  `disease`, `medication_class`, ...). Legacy single `kind` still accepted.
- `parent_slug`?, `coding_system`?, `code`?, `aliases`? (list), `description`?,
  `icon`? (`{type:"lucide", value:"IconName"}`), `color`?, `display_order`?

### `concept_edges.json` — polymorphic knowledge-graph edges
- `src_slug` **(req)**, `src_type`? (default `concept`;
  `concept` | `anatomy` | `biomarker` | `medication` | `vaccine`)
- `dst_slug` **(req)**, `dst_type`? (default `concept`)
  - `medication` resolves `MedicationCatalog` by case-insensitive **name** (no slug column)
  - `vaccine` resolves `VaccineCatalog` by **slug**
- `relation` **(req)** — a `ConceptRelationType`: `MEMBER_OF`, `HAS_SPECIALTY`,
  `CLASSIFIED_AS`, `EXAMINES`, `IMAGES`, `PERFORMS`, `ORDERS`, `LOCATED_IN`,
  `PART_OF`, `AFFECTS`, `TREATS`, `INDICATES`, `PREVENTS`, `CONTRAINDICATES`,
  `CORRELATES_WITH`, `CAUSED_BY`, `MONITORS`, `RISK_OF`, `SCREENS_FOR`,
  `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`,
  `SUPPLIED_BY`, `CONTINUOUS_WITH`

### `diseases.json` — disease reference concepts (`kind=disease`)
Same item shape as `concepts.json` (it's loaded by the same `_process_concepts`
upsert logic). Diseases ship in a separate file so the curated ICD-10 reference
is independently maintainable. Must run AFTER `seed_concepts` and BEFORE
`seed_concept_edges` (so specialty/medication/vaccine → disease edges resolve).
- `slug` **(req)**, `name` **(req)**, `kinds` **(req)** (`["disease"]`)
- `coding_system`? (`icd10`), `code`? (ICD-10 code, e.g. `E11.9`),
  `aliases`? (list), `description`?, `icon`?, `color`?

### `anatomy_structures.json` — body-part nodes
- `slug` **(req)**, `name` **(req)**
- `class_concept_slug`? **(preferred, lossless)** — an `anatomy_class` concept
  slug. Resolves directly; round-trips any class including ones outside the
  legacy enum.
- `category`? **(legacy)** — uppercase enum (`SYSTEM`, `REGION`, `ORGAN`,
  `ORGAN_PART`, `TISSUE`, `JOINT`, `CELL`, `SUBSTANCE`, `OTHER`), mapped to a
  fixed concept slug. Accepted for backward compat; `class_concept_slug` wins
  when both are present.
- `standard_system`? (`loinc` | `snomed` | `custom`), `standard_code`?,
  `description`?, `display`? (JSONB; holds body-map markers under
  `display.map.markers[<figure-slug>] = {nx, ny, nr}` — normalized 0–1)

### Anatomy hierarchy edges (formerly `anatomy_relations.json`)
The separate `anatomy_relations.json` file is **deleted**. Anatomy hierarchy
edges now live in `concept_edges.json` with `src_type=anatomy`,
`dst_type=anatomy`. The relation types (`PART_OF`, `BRANCH_OF`, `DRAINS_INTO`,
`ARTICULATES_WITH`, `INNERVATED_BY`, `SUPPLIED_BY`, `CONTINUOUS_WITH`) are
`ConceptRelationType` values.

### `default_catalog.json` — biomarker units + definitions
- `units[]`: `symbol` **(req)**, `name` **(req)**, `quantity_type`?
  (`MASS_CONCENTRATION`, `MOLAR_CONCENTRATION`, `NUMBER_CONCENTRATION`,
  `PERCENTAGE`, `PRESSURE`, `VOLUME`, `MASS`, `TIME`, `RATIO`, `TEMPERATURE`,
  `OTHER`; default `OTHER`)
- `biomarkers[]`: `slug` **(req)**, `name` **(req)**, `coding_system`?
  (default `loinc`), `code`?, `class_concept_slug`? **(preferred, lossless — a
  `biomarker_class` concept slug)**, `category`? (legacy underscore string,
  e.g. `blood_laboratory`; swapped `_`→`-` to find the concept),
  `preferred_unit_symbol`?, `aliases`? (list), `info`?,
  `reference_range_min`?, `reference_range_max`?, `is_telemetry`?

### `biomarker_panels.json` — panel memberships (→ `MEMBER_OF` edges)
- `panel_slug` **(req)** — a `biomarker_panel` concept
- `biomarker_slug` **(req)** — a `BiomarkerDefinition` slug

### `clinical_event_types.json` — longitudinal health-journey blueprints
- `slug` **(req)**, `name` **(req)**, `category_slug`? (an `event_category`
  concept), `description`?, `icon`?, `color`?, `metadata_schema`?
  (`{fields:[{name,label,type(text|number|date|boolean),required?}]}`)

### `medications.json` — medication reference catalog
- `name` **(req)**, `description`?, `indications`?, `side_effects`? (list),
  `contraindications`?, `dosage_info`?

### `allergies.json` — allergy reference catalog
- `name` **(req)**, `category` **(req)** — `FOOD` | `MEDICATION` |
  `ENVIRONMENT` | `BIOLOGIC` | `OTHER`, `description`?, `typical_reactions`?
  (list)

## Editing workflow

1. Edit the JSON (or build it in a running instance and export — see below).
2. Restart the app, or re-run the one stage:
   ```bash
   cd backend && source venv/bin/activate && export PYTHONPATH=.:../
   python -c "import asyncio; from app.core.database import AsyncSessionLocal; from app.services.seed_service import seed_service; asyncio.run(seed_service.seed_concepts())"
   ```
   (replace `seed_concepts` with any `_SEED_STAGE_NAMES` method).

## Exporting an instance back to seeds

`SeedExportService` (`backend/app/services/seed_export_service.py`) is the
inverse of `SeedService` — it snapshots a running instance's data into this
slug-keyed format. Deterministic output; safe write (backup + atomic).

```bash
python scripts/export_seeds.py --dry-run           # preview counts, no writes
python scripts/export_seeds.py                     # global taxonomy -> here (backed up)
python scripts/export_seeds.py --source TENANT_ID  # a template tenant as source
python scripts/unpack_seeds_zip.py seeds.zip       # unpack a downloaded ZIP (backup + extract)
```

For an instance on another machine: Catalogs workspace → **Export seeds** button
(SYSTEM_ADMIN) downloads a ZIP → transfer → `unpack_seeds_zip.py` →
`git diff data/seeds/` → review → commit.

## Not exported / out of scope

- `anatomy_figures/` — binary WebP images, managed via the Atlas Editor.
- `sample_blood_panel.pdf` — a demo OCR fixture, not a seed.

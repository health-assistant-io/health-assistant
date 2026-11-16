# Seed Files

Health Assistant's reference/catalog data ships as JSON seed files in this
directory — wrapped as the **bundled seed pack** (one manifest + per-file
sha256 pins). The pack is a **trusted, auto-approved source**
(`seed_sources` kind `bundled`, priority 0): boot re-checks its digest and
applies it through the pack planner/applier **only when the digest
changed** (audited, per-stage transactions; a fresh DB seeds fully before
any admin exists). The pack is parsed and validated by
`app.core.seed_pack` and dispatched by
`SeedService.seed_all()` (`backend/app/services/seed/service.py`) — a single
ordered, idempotent pipeline whose seeders take the caller's session and
never commit (the applier commits per pack stage). Re-running a
stage reconciles existing rows to the pack (upsert by deterministic id,
then `seed_key` — never by name or slug), so editing a file and restarting
is the normal way to evolve catalog data: the digest changes, the next
boot applies the new version. Precedence, provenance, retirement,
deferred references and the `user_modified` freeze are documented in
[`docs/SEEDING_AND_DEMOS.md` §0b](../../../docs/SEEDING_AND_DEMOS.md).

## Envelope

Every file (except `default_catalog.json`) uses the standard envelope:

```json
{ "metadata": { "version": "1.0.0", "source": "...", "last_updated": "YYYY-MM-DD", "count": N },
  "items": [ ... ] }
```

`last_updated` is **optional on ingest**: the canonical export writes no
timestamps, so a re-export of an unchanged DB is a
byte-level no-op. The export covers every file in this directory — including
`biomarker_states.json` and `omb_race_ethnicity.json` (the OMB picklist
flows through the pack path, so its export is a normalized pass-through) —
wrapped as the canonical pack zip (`manifest.json` + `items/*.json`,
sha256-verified on unpack by `scripts/unpack_seeds_zip.py`).

`default_catalog.json` uses `{ metadata, units[], biomarkers[] }`.
`omb_race_ethnicity.json` is a picklist (not DB rows): `{ races[], ethnicities[], languages[] }`.
The pack parser normalizes each file to its registered collections (the
coverage matrix in `app/core.seed_pack.items`), so seeders always consume
lists.

## Pack contract

`uv run python scripts/seed_pack.py pack --out /tmp/bundled-pack` builds the
bundled pack from this directory (manifest + raw byte copies, **no
timestamps** — the same content always yields the same manifest and the same
whole-pack digest); `validate` and `digest` check any pack. Every `*.json`
file here must be a registered pack item type — an unregistered file fails
the build loudly. Pack items: concepts, diseases, medications, vaccines,
clinical event types, allergies, anatomy structures (body parts), biomarker
states, the default catalog, concept edges, biomarker panels and the OMB
picklist. **Not pack items**: `retention_policies`, `proposal_kinds`,
`ai_task_defs` (code/contract vocabularies seeded by the app) and the
binary `anatomy_figures/` (bundled-only in v1). Tenant packs may only
target tenant-ownable types (medications, vaccines, allergies, clinical
event types); global-only types in a tenant pack are a typed
`GLOBAL_ONLY_ITEM_TYPE` error. Remote sources later feed the same lane:
versions are **digest-pinned and approved before apply**, and a pack that
drops an item has it **retired, never deleted** (per-row provenance records
the state). See
[docs/SEEDING_AND_DEMOS.md §The seed pack](../../../docs/SEEDING_AND_DEMOS.md#the-seed-pack-delivery-unit)
for the full matrix, caps and field rules.

## Identity model

**Every curated item carries a mandatory `seed_key`** — the immutable
qualified natural key `<type>:<kebab-key>` (`medication:amoxicillin`,
`concept:diabetes`, `unit:mg-per-dl`, `panel-membership:lipid-panel/cholesterol-total`).
The row id is derived from it deterministically:
`uuid5(SEED_KEY_NAMESPACE, "<type>:<seed_key>")` — reseeds are id-stable and
independent of insertion order. The key is written once and never renamed (a
rename is a new row, not an edit).

- **`slug`/`name` are display data** — kept where the table has a slug
  (vaccine, concept, anatomy, event types, states), never referenced as a
  join key. Medication/allergy carry no slug at all.
- **Codes are attributes**: `coding_system` + `code` update only when an
  item provides them; no code is ever invented (allergy items ship codes
  `null` until a curator adds verified SNOMED substance codes).
- **Optional `id`** on an item overrides the derived id at creation (pack
  import); an id owned by a different seed key is rejected.
- Cross-references (`parent_slug`, `src_slug`/`dst_slug`, `panel_slug`,
  `class_concept_slug`, `category_slug`) resolve to the referenced rows'
  identity ids at load time.
- Upserts are system-scoped only: every lookup filters `tenant_id IS NULL`,
  so a tenant overlay reusing a seed key is never touched by a seed run.

The helper behind all of this is `backend/app/services/seed/identity.py`;
the rules are normative in
[docs/SEEDING_AND_DEMOS.md §Seed identity](../../../docs/SEEDING_AND_DEMOS.md#the-identity-of-a-catalog-row-seeder-rules).

### Hard reseed

`uv run python scripts/seed.py --reset-curated` deletes the curated **system**
rows of these catalogs and reseeds them with the deterministic ids. Tenant
rows are never deleted, but the reset **refuses** (typed error) while any
live row still references the system rows it would delete — RESTRICT FKs
(tenant event types on system category concepts, tenant biomarker
allowed-state rows on global states) and the `SET NULL`/`CASCADE` FKs from
instance data (catalog-linked medication statements, allergy records and
immunizations, clinical-event anatomy links/occurrences, tenant concept
children of system parents), since a delete would silently null or destroy
those references. Run it once on installs created before the identity model
(rows with random ids) — safe because there are no users.

## Identity model

**The `seed_key` is the stable natural key; `slug`/`name` are display
data.** Every cross-reference (`parent_slug`, `src_slug`/`dst_slug`,
`panel_slug`, `class_concept_slug`, `category_slug`) resolves to the
referenced rows' identity (derived uuid5, then the `seed_key` column) at
load time — never by name or slug. See
[docs/SEEDING_AND_DEMOS.md §7](../../../docs/SEEDING_AND_DEMOS.md#7-exporting-an-instance-back-to-seeds).

## Field schemas

Required fields are marked **(req)**. `?` = optional.

### `concepts.json` — unified taxonomy nodes
- `seed_key` **(req)** — `concept:<slug>`
- `slug` **(req)**, `name` **(req)**
- `kinds` **(req)** — list of `ConceptKind` values (`specialty`,
  `examination_category`, `biomarker_class`, `biomarker_panel`,
  `anatomy_class`, `event_category`, `document_category`, `body_system`,
  `disease`, `medication_class`, ...). Legacy single `kind` still accepted.
- `parent_slug`?, `coding_system`?, `code`?, `aliases`? (list), `description`?,
  `icon`? (`{type:"lucide", value:"IconName"}`), `color`?, `display_order`?

### `concept_edges.json` — polymorphic knowledge-graph edges
- `src_slug` **(req)**, `src_type`? (default `concept`;
  `concept` | `anatomy` | `biomarker` | `medication` | `vaccine` — the
  exported `immunization` tag is accepted as an alias of `vaccine`)
- `dst_slug` **(req)**, `dst_type`? (default `concept`)
  - every ref resolves **by identity** (the uuid5 of
    `medication:<kebab-key>` / `vaccine:<slug>` / `biomarker:<slug>` /
    `anatomy:<slug>` / `concept:<slug>`, then the row's `seed_key`
    column) — never by name or slug; medication has no slug, its ref is the
    seed-key key part
  - only an existing **approved** tuple short-circuits; a pending/rejected
    proposal between the same endpoints never suppresses the curated edge
- `relation` **(req)** — a `ConceptRelationType`: `MEMBER_OF`, `HAS_SPECIALTY`,
  `CLASSIFIED_AS`, `EXAMINES`, `IMAGES`, `PERFORMS`, `ORDERS`, `LOCATED_IN`,
  `PART_OF`, `AFFECTS`, `TREATS`, `INDICATES`, `PREVENTS`, `CONTRAINDICATES`,
  `CORRELATES_WITH`, `CAUSED_BY`, `MONITORS`, `RISK_OF`, `SCREENS_FOR`,
  `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`,
  `SUPPLIED_BY`, `CONTINUOUS_WITH`

### `diseases.json` — disease reference concepts (`kind=disease`)
Same item shape as `concepts.json` (including the mandatory `seed_key` = `concept:<slug>`) (it's loaded by the same `_process_concepts`
upsert logic). Diseases ship in a separate file so the curated ICD-10 reference
is independently maintainable. Must run AFTER `seed_concepts` and BEFORE
`seed_concept_edges` (so specialty/medication/vaccine → disease edges resolve).
- `slug` **(req)**, `name` **(req)**, `kinds` **(req)** (`["disease"]`)
- `coding_system`? (`icd10`), `code`? (ICD-10 code, e.g. `E11.9`),
  `aliases`? (list), `description`?, `icon`?, `color`?

### `anatomy_structures.json` — body-part nodes
- `seed_key` **(req)** — `anatomy:<slug>` (no `seed_key` column on the table; the key drives the deterministic id)
  **Never rename a node's `slug`**: the key is derived from it
  (`anatomy:<slug>`), so a rename is a new identity — a new row, and every
  edge referencing the old slug skips until re-pointed.
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
- `units[]`: `seed_key` **(req)** — `unit:<kebab-symbol>` (e.g. `unit:mg-per-dl`, `unit:percent`), `symbol` **(req)**, `name` **(req)**, `quantity_type`?
  (`MASS_CONCENTRATION`, `MOLAR_CONCENTRATION`, `NUMBER_CONCENTRATION`,
  `PERCENTAGE`, `PRESSURE`, `VOLUME`, `MASS`, `TIME`, `RATIO`, `TEMPERATURE`,
  `OTHER`; default `OTHER`). An item without a `seed_key` (an export of a
  running instance) still carries its `id`; a fresh install recreates the
  unit under that id.
- `biomarkers[]`: `seed_key` **(req)** — `biomarker:<slug>`, `slug` **(req)**, `name` **(req)**, `coding_system`?
  (default `loinc`), `code`?, `class_concept_slug`? **(preferred, lossless — a
  `biomarker_class` concept slug)**, `category`? (legacy underscore string,
  e.g. `blood_laboratory`; swapped `_`→`-` to find the concept),
  `preferred_unit_symbol`?, `aliases`? (list), `info`?,
  `reference_range_min`?, `reference_range_max`?, `is_telemetry`?,
  `value_type`? (`quantity` default, or `state` for categorical biomarkers),
  `supports_multi_state`? (STATE-only; accept `component[]` observations),
  `allowed_states`? (STATE-only; list of `{state_slug, is_normal, sort_order}`
  referencing `biomarker_states.json`)

### `biomarker_states.json` — universal state catalog (STATE biomarkers)
- `items[]`: `seed_key` **(req)** — `biomarker-state:<slug>` (no column; drives the deterministic id), `slug` **(req, unique)**, `code` **(req)**, `system` **(req)**
  (code system URL: `http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation`,
  `http://snomed.info/sct`, `http://terminology.hl7.org/CodeSystem/data-absent-reason`,
  or `urn:uuid:health-assistant:custom-state`), `display` **(req)**,
  `description`?, `category`? (group label for UI navigation),
  `sort_order`? (int, default 0)
- Unique on `(code, system)`. Seeded with 22 codes in 5 categories
  (Microbiology & Serology, Antimicrobial Susceptibility, Qualitative Presence,
  Limits & Thresholds, Data Absent). See [STATE_BIOMARKERS.md](../../../docs/STATE_BIOMARKERS.md).

### `biomarker_panels.json` — panel memberships (→ `MEMBER_OF` edges)
- `seed_key` **(req)** — `panel-membership:<panel-slug>/<biomarker-slug>`; the created edge row carries it as its deterministic id
- `panel_slug` **(req)** — a `biomarker_panel` concept
- `biomarker_slug` **(req)** — a `BiomarkerDefinition` slug

### `clinical_event_types.json` — longitudinal health-journey blueprints
- `seed_key` **(req)** — `clinical-event-type:<slug>` (no column; drives the deterministic id).
  **Never rename a `slug`**: the key is derived from it, so a rename is a new
  identity (a new row), not an edit.
- `slug` **(req)**, `name` **(req)**, `category_slug`? (an `event_category`
  concept), `description`?, `icon`?, `color`?, `metadata_schema`?
  (`{fields:[{name,label,type(text|number|date|boolean),required?}]}`)

### `medications.json` — medication reference catalog
- `seed_key` **(req)** — `medication:<kebab-name>`
- `coding_system`?/`code`? — real ATC codes (e.g. Metformin `atc`/`A10BA02`) or both omitted — never invented
- `name` **(req)**, `description`?, `indications`?, `side_effects`? (list),
  `contraindications`?, `dosage_info`?

### `allergies.json` — allergy reference catalog
- `seed_key` **(req)** — `allergy:<kebab-name>`; `coding_system`/`code` stay null until verified SNOMED substance codes exist
- `name` **(req)**, `category` **(req)** — `FOOD` | `MEDICATION` |
  `ENVIRONMENT` | `BIOLOGIC` | `OTHER`, `description`?, `typical_reactions`?
  (list)

## Editing workflow

1. Edit the JSON (or build it in a running instance and export — see below).
   Every item must keep its `seed_key`; new items get a fresh stable
   `<type>:<kebab-key>` (written once, never renamed). In
   `anatomy_structures.json` and `clinical_event_types.json` the key is
   mechanical (`<type>:<slug>`) — **never rename a slug there**, a rename
   forks the identity instead of editing the row.
2. Check the pack still validates (the bundled build validates too —
   duplicates, caps, field rules):
   ```bash
   cd backend && uv run python scripts/seed_pack.py pack --out /tmp/bundled-pack
   ```
3. Restart the app, or re-run the whole pipeline (idempotent, only real
   changes are written):
   ```bash
   cd backend && uv run python scripts/seed.py
   ```
   To replace the curated **system** rows entirely (deterministic re-identity):
   ```bash
   cd backend && uv run python scripts/seed.py --reset-curated
   ```

## Exporting an instance back to seeds

`SeedExportService` (`backend/app/services/seed/seed_export_service.py`) is the
inverse of `SeedService` — it snapshots a running instance's data into this
identity-keyed format (every item with its `id`; curated rows with their
`seed_key`). Deterministic output; safe write (backup + atomic).

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

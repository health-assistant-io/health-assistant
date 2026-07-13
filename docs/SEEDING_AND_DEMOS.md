# Seeding & Demos

Creating demo data is critical for E2E tests, documentation screenshots, and local development. This project follows specific best practices to ensure seeding is idempotent, deterministic, and maintainable.

## 0. The Seed Pipeline (`SeedService.seed_all`)

Every application startup runs a single ordered, idempotent seed pipeline against the dev/prod DB (`backend/app/services/seed_service.py`). The stage order is declared in `_SEED_STAGE_NAMES` so dependencies land first:

| # | Stage | Depends on |
|---|---|---|
| 1 | `medications` | — |
| 2 | `clinical_event_types` | — |
| 3 | `allergies` | — |
| 4 | `body_parts` (anatomy structures) | — |
| 5 | `anatomy_figures` | body_parts |
| 6 | `concepts` (taxonomy) | — |
| 7 | `concept_edges` (incl. concept→anatomy + anatomy→anatomy) | concepts + body_parts |
| 8 | `default_catalog` (units + biomarker definitions) | concepts (biomarker_class) |
| 9 | `biomarker_panels` (MEMBER_OF edges) | concepts + default_catalog |

**Conventions:**
- **Envelope** — every seed JSON is `{ "metadata": {...}, "items": [...] }`. (The legacy `anatomy_base.json` was split into `anatomy_structures.json` + `anatomy_relations.json`; the latter is now **deleted** — anatomy hierarchy edges live in `concept_edges.json` with `src_type=anatomy, dst_type=anatomy`. `biomarker_panels.json` migrated from a `{panel: [slugs]}` map to the standard items list.)
- **Stats contract** — every `seed_*` returns `{added, updated, skipped, errors}`. `seed_default_catalog` additionally carries a `details: {units_added, units_updated, biomarkers_added, biomarkers_updated}` sub-dict.
- **Idempotency + reconciliation** — re-runs upsert by the natural key (slug / `(src, dst, relation)` for edges) and reconcile existing rows to the JSON. Editing a concept's `kinds` array and restarting will diff its kind tags into place; nothing is ever deleted by a seed.
- **`main.py`** calls `seed_all()` in one place; the whole pipeline is wrapped in `_abort_or_warn` (fail-soft in dev, abort boot in prod).

To re-run a single stage against a running DB without restarting, see [DEVELOPMENT.md §Seed System](DEVELOPMENT.md).

## 1. Avoid Raw SQL or `.zip` Imports for Core Seeds

While the system supports ZIP and JSON imports for end-users, relying on external `.zip` files for core developer seeding introduces complexity:
- Binary files are hard to review in Git.
- Schema changes break `.zip` payloads silently until runtime.
- Complex ID mapping is required.

**Best Practice:** Use the `ImportService` programmatically with an embedded FHIR Bundle in a Python script (like `backend/scripts/seed_demo.py`). This gives you:
- Complete control over deterministic dates.
- Type safety and stack traces if schema changes occur.
- Immediate feedback during development.

## 2. Deterministic Execution

Seeds must be **idempotent**. Running `python3 scripts/seed_demo.py` multiple times must not create duplicate tenants, users, or patients.
- Use `scalar_one_or_none()` to check for existing records (e.g., matching a fixed `slug` or `email`).
- Check for existing data (e.g., `Patient.mrn` or `Observation.subject`) before bulk-inserting.

## 3. Date Freezing for Visual Regression

When generating data for screenshots (e.g., `capture_ui.sh`), dates must be relative to a "frozen" present.
In this project, the UI capture scripts (`frontend/tests-e2e/ui-capture/capture.mjs`) freeze the browser clock (e.g., to `2026-06-15`).
Your seed script should generate FHIR resources with absolute dates relative to that same frozen point to ensure charts and relative times ("2 days ago") render identically across runs.

## 4. The Seed Data Structure

A robust clinical seed should use the internal `ImportService.restore_fhir_bundle` and include:
1. **Tenant & User**: A dedicated demo tenant and at least one Admin user.
2. **Patients**: At least one primary patient with a predictable MRN.
3. **Biomarkers**: `Observation` resources mapped to standard LOINC codes (the system will auto-resolve these).
4. **Medications**: `MedicationStatement` resources.
5. **Allergies**: `AllergyIntolerance` resources.
6. **Examinations**: Sideloaded via `restore_sidecar("examinations.json", ...)` to provide clinical context notes.

## 5. UI Patient Selection

By design, Admin users in Health Assistant have access to all patients in a tenant and do **not** have a default patient context automatically selected on login.
If you see "No patient selected" in the Dashboard or AI Chat:
- **During manual testing:** Use the Patient Context Switcher (usually in the header or sidebar) to select your demo patient.
- **During automated screenshots:** The E2E script (`capture.mjs`) automatically injects the primary patient into the `patient-storage` Zustand store via `addInitScript` before rendering the page.

## 6. Anatomy Graph Catalog

The anatomy graph is a modular, directed-graph ontology of the human body. It replaces the old flat `body_parts` table with two interconnected models:

- **`AnatomyStructure`** (nodes): body parts, organs, systems, tissues, cells, joints, etc.
- **`AnatomyRelation`** (edges): **deleted** — replaced by `concept_edges` rows (`src_type='anatomy'`, `dst_type='anatomy'`). The relation types (`PART_OF`, `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`, `SUPPLIED_BY`, `CONTINUOUS_WITH`) are now `ConceptRelationType` values.

Each node carries a `class_concept_slug` referencing an `anatomy_class` concept (e.g. `organ`, `system`, `region`, `organ-part`, `tissue`, `joint`) and optional standard identifiers (`standard_system` + `standard_code` for SNOMED CT, LOINC, or CUSTOM coding). The legacy `category` enum (`SYSTEM`, `REGION`, `ORGAN`, …) was replaced by the unified taxonomy — the import service maps a `class_concept_slug` to a `class_concept_id` FK.

### 6.1 Automatic Seeding on Startup

The base anatomy catalog is seeded automatically on application startup by `SeedService.seed_all()` (stage `body_parts`). It reads one standard-envelope seed file for nodes:

```
backend/data/seeds/anatomy_structures.json   (nodes — AnatomyStructure rows)
```

Anatomy hierarchy **edges** are no longer a separate file — `anatomy_relations.json` is **deleted**; the edges now live in `concept_edges.json` (seeded at the `concept_edges` stage) with `src_type=anatomy, dst_type=anatomy`. The structures file contains **54 nodes** (major body systems, organs, regions, and joints with SNOMED codes). The seeding is idempotent — existing nodes are updated by slug, new ones are inserted, and duplicate edges are skipped.

The startup log (emitted by `seed_all`) shows:
```
Seeding body_parts...
Seeded body_parts: {'added': 54, 'updated': 0, 'skipped': 0, 'errors': 0}
```

### 6.2 Manual Re-seed

To re-seed the base anatomy catalog manually (e.g., after editing the split seed files), call the service method directly against a running backend:

```bash
cd backend
source venv/bin/activate
export PYTHONPATH=.:../
python -c "import asyncio; from app.core.database import AsyncSessionLocal; from app.services.seed_service import seed_service; asyncio.run(seed_service.seed_body_parts())"
```

(The `scripts/seed_anatomy.py` CLI is for importing *custom expansion packs* — see §6.3 — not for re-seeding the base catalog.)

### 6.3 Importing Custom Expansion Packs

Custom anatomy packs (e.g., a detailed ophthalmology or neuroanatomy graph) can be imported from a local JSON file or a URL using the same script:

```bash
# From a local file
python scripts/seed_anatomy.py --file path/to/my-anatomy-pack.json

# From a URL
python scripts/seed_anatomy.py --url https://example.com/anatomy-pack.json
```

In Docker:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/seed_anatomy.py --file /path/to/pack.json
```

Alternatively, use the REST API endpoint `POST /api/v1/anatomy/import` (SYSTEM_ADMIN only) with the same JSON payload format.

```json
{
  "nodes": [
    {
      "slug": "left-ventricle",
      "name": "Left Ventricle",
      "class_concept_slug": "organ-part",
      "standard_system": "snomed",
      "standard_code": "87878005",
      "description": "The lower left chamber of the heart",
      "is_custom": false,
      "display": {
        "map": {
          "markers": {
            "man-front": { "nx": 0.55611, "ny": 0.35823, "nr": 0.06984 }
          }
        }
      }
    }
  ],
  "edges": [
    {
      "source_slug": "left-ventricle",
      "target_slug": "heart",
      "relation_type": "PART_OF"
    }
  ]
}
```

**Import rules:**
- **Nodes** are upserted by `slug`. If a node with the same slug exists, it is updated; otherwise, it is created. Records are never deleted.
- **Edges** are deduplicated by the unique constraint `(source_id, target_id, relation_type)`. Duplicate edges are silently skipped. (The former `AnatomyRelation` table and this schema are replaced by `concept_edges` rows — `src_type=anatomy, dst_type=anatomy`.)
- Edge `source_slug` and `target_slug` must resolve to existing nodes (either in the payload or already in the database). Unresolvable edges are skipped with a warning.
- `class_concept_slug` is optional and must resolve to an existing `anatomy_class` concept (e.g. `organ`, `system`, `region`, `organ-part`, `tissue`, `joint`). This is the only accepted form — the legacy uppercase `category` enum (`SYSTEM`, `REGION`, `ORGAN`, …) was removed (clean rebuild); seed files must carry `class_concept_slug` directly.
- `relation_type` must be one of: `PART_OF`, `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`, `SUPPLIED_BY`, `CONTINUOUS_WITH`.
- `standard_system` is optional and must be one of: `loinc`, `snomed`, `custom`.
- `display` is optional JSONB metadata. It holds body-map rendering hints under `display.map.markers`, **keyed by figure slug** (e.g. `man-front`, `woman-back` — slugs of rows in the `anatomy_figures` table). Each marker uses **normalized** coordinates (`nx`, `ny`, `nr` in the 0–1 range) relative to that figure's own viewBox. Absolute pixel coordinates are not used — positions are figure-independent and resolved at render time. See **§6.6**.

### 6.4 AI-Driven Graph Expansion

The system includes an AI orchestrator for on-demand anatomy graph generation. System admins can ask the AI chatbot to generate a detailed anatomical sub-graph (e.g., "Generate the anatomy of the cardiovascular system in detail"). The AI uses the `propose_anatomy_graph_generation` tool, which creates a HITL (human-in-the-loop) review card. Upon user confirmation, the AI generates the graph nodes and edges, validates them, and imports them via the same JSON import pipeline.

The AI task type `define_anatomy_graph` can also be called directly via `POST /api/v1/ai-assistance/assist` with a `user_input` describing the target structure.

### 6.5 Adding New Seed Nodes

To extend the base anatomy catalog that ships with the application:

1. Edit `backend/data/seeds/anatomy_structures.json` (add nodes to `items`) and/or `backend/data/seeds/concept_edges.json` (add anatomy→anatomy edges to `items`, with `src_type=anatomy, dst_type=anatomy`).
2. Ensure node `slug` values are unique, and that edge `source_slug`/`target_slug` reference existing slugs.
3. Restart the application (or run the manual re-seed command in §6.2). The upsert logic will add new nodes and edges without affecting existing data.

### 6.6 Body Diagram Atlas & Organ Markers

The Anatomy Explorer renders a 2D human body and overlays organ markers so users can locate structures visually. The atlas is **fully DB-driven, image-based, and admin-manageable**:

- **`anatomy_figures` table** — each row is one view of one figure (e.g. `man-front`, `woman-back`) backed by a **WebP image** stored on disk under `UPLOAD_DIR/anatomy_figures/`. No SVG markup or viewBox — the stored image IS the view. The four defaults ship as bundled WebP seeds (rasterized from the Wikimedia surface diagrams at 3×, CC BY-SA 3.0 — see `NOTICE`) and are copied to `UPLOAD_DIR` at startup.
- **Per-figure markers** — every structure may carry `display.map.markers[<figure-slug>] = { nx, ny, nr }`, where `nx`/`ny`/`nr` are fractions of the image's pixel dimensions (0–1). The marker overlay SVG uses the image's `width × height` as its viewBox, so it aligns with the `<img>` by construction at any render size.
- **Frontend store** — `useAnatomyAtlas` (Zustand, `frontend/src/components/anatomy/atlas.ts`) fetches the figure list once and lazily fetches each image as an authenticated blob → object URL.

**Atlas Manager (SYSTEM_ADMIN only)** — `/admin/anatomy-atlas`. Create/edit/delete figures with an interactive image cropper: upload a source image, drag to select the view region (real pixel crop via `<canvas>` → WebP), preview live, and save — all without code changes. To add a new view (e.g. a left-side aspect): *Add Figure* → pick a figure group (`man`) and view (`left`) → upload the source image → crop → save. Markers can then be placed on the new figure via the Position Editor.

**Position Editor (SYSTEM_ADMIN only)** — the canonical way to place or fix a structure's marker. From the Anatomy Explorer, click **Positions** to open the drag-and-drop editor:

1. Pick the figure from the picker (grouped by figure group → views).
2. Search a structure in the sidebar and toggle its marker **ON**.
3. Drag the dot onto the body — normalized coordinates update live and **auto-save** via `PATCH /api/v1/anatomy/{slug}` (writing `display.map.markers[figure-slug]`).

No pixel math or JSON editing is required.

## 7. Exporting an Instance Back to Seeds

Seed files are normally hand-edited JSON. But it's often easier to build the
canonical taxonomy / anatomy / biomarker catalog in a running instance — via
the Catalogs workspace, the Anatomy Atlas editor, the AI chatbot's graph-
generation tools, or the ontology-catalog import — and then **snapshot that
instance's data back into the shipped seed format**. `SeedExportService`
(`backend/app/services/seed_export_service.py`) is the strict inverse of
`SeedService`: it reads the DB and emits the slug-keyed `{metadata, items}`
envelope each `seed_*` stage consumes, covering **all eight seed files** —
concepts, concept edges, anatomy structures, the default catalog,
biomarker panels, clinical event types, medications, and allergies.

The output is **deterministic** (sorted by slug/name, fixed field order, only
meaningful fields, a regenerated `metadata` block), so re-exporting an
unchanged DB is a git no-op — the point is to review diffs, not blind-replace.

### 7.1 Identity model

**Slug is the stable join key; `name` is mutable display.** Cross-references
between seed items are by slug (`parent_slug`, `src_slug`/`dst_slug`,
`panel_slug`, `class_concept_slug`). Where an entity has a real external code,
`coding_system` + `code` are emitted alongside as **identity evidence**
(FHIR-aligned, useful for interop/dedup audits) but are *not* the join key —
see the design discussion in `dev/plans/seed-export-service-2026-07-07.md`.

### 7.2 Three delivery surfaces (same service underneath)

**UI download (SYSTEM_ADMIN)** — the **Catalogs workspace** toolbar
(`/catalogs`) has an **Export seeds** button that calls
`GET /api/v1/admin/seeds/export.zip` and saves a ZIP of the eight files. The
download is **read-only** — the server never writes its own `data/seeds/`.

**CLI (curator on the same machine as the DB)**:
```bash
cd backend && source venv/bin/activate && export PYTHONPATH=.:../
python scripts/export_seeds.py --dry-run    # preview counts, no writes
python scripts/export_seeds.py              # global taxonomy -> data/seeds
python scripts/export_seeds.py --source TENANT_ID   # a template tenant
python scripts/export_seeds.py --out /tmp/seeds     # custom output dir
```
The write is safe: files stage to `.export-staging/`, existing files back up
to `.backup-<timestamp>/`, then atomic write.

**Dev-side unpack helper** — for the transfer workflow (instance on another
machine): download the ZIP via the UI, copy it to your dev machine, then:
```bash
python scripts/unpack_seeds_zip.py path/to/health-assistant-seeds.zip
```
Backs up existing `data/seeds/*.json` to `.backup-<timestamp>/`, then extracts.
Refuses archives containing unknown or path-traversal entries.

### 7.3 The transfer workflow (instance on another machine)

1. On the running instance: Catalogs workspace → **Export seeds** → `health-assistant-seeds.zip`.
2. Transfer the ZIP to your dev machine (download, scp, USB).
3. `python scripts/unpack_seeds_zip.py health-assistant-seeds.zip` (backup + extract).
4. `git diff data/seeds/` → review → commit. The next release ships the new seeds.

### 7.4 Source scope

`--source global` (the default) exports `tenant_id IS NULL` rows — the global
taxonomy that ships. Pass a tenant UUID (`--source TENANT_ID` or
`SeedExportService(db, tenant_id=...)`) to treat a dedicated "template tenant"
as the source: its rows are emitted with scope stripped. This is how you curate
the canonical set in a tenant you control via the UI, then promote it to global
seeds.

### 7.5 Binary anatomy assets note

Positional marker data lives in `AnatomyStructure.display` (JSONB) and is
carried by the `anatomy_structures.json` export. Binary `anatomy_figures/`
images are out of scope for the seed ZIP (managed via the Atlas Editor).

See `backend/data/seeds/README.md` for the per-file field-schema cheatsheet.


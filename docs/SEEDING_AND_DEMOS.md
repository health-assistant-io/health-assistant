# Seeding and Demos

Creating robust demo data is critical for E2E tests, documentation screenshots, and local development. This project follows specific best practices to ensure seeding is idempotent, deterministic, and maintainable.

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
- **`AnatomyRelation`** (edges): relationships between structures (`PART_OF`, `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`, `SUPPLIED_BY`, `CONTINUOUS_WITH`).

Each node has a `category` (SYSTEM, REGION, ORGAN, ORGAN_PART, TISSUE, CELL, SUBSTANCE, JOINT, OTHER) and optional standard identifiers (`standard_system` + `standard_code` for SNOMED CT, LOINC, or CUSTOM coding).

### 6.1 Automatic Seeding on Startup

The base anatomy catalog is seeded automatically on application startup (in `main.py` lifespan) via `seed_service.seed_body_parts()`. The seed file is:

```
backend/data/seeds/anatomy_base.json
```

This file contains **54 nodes** (major body systems, organs, regions, and joints with SNOMED codes) and **67 edges** (PART_OF and SUPPLIED_BY relationships). The seeding is idempotent — existing nodes are updated by slug, new ones are inserted, and duplicate edges are skipped.

The startup log will show:
```
Syncing body parts catalog...
Body parts sync complete: {'added': 54, 'updated': 0, 'errors': 0}
```

### 6.2 Manual Seeding

To re-seed the base anatomy catalog manually (e.g., after modifying `anatomy_base.json`):

```bash
cd backend
source venv/bin/activate
export PYTHONPATH=.:../
python scripts/seed_anatomy.py
```

In Docker:

```bash
# Standalone
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend python scripts/seed_anatomy.py

# Prod
docker compose --env-file .env -f docker/docker-compose.prod.yml exec backend python scripts/seed_anatomy.py
```

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
      "category": "ORGAN_PART",
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
- **Edges** are deduplicated by the unique constraint `(source_id, target_id, relation_type)`. Duplicate edges are silently skipped.
- Edge `source_slug` and `target_slug` must resolve to existing nodes (either in the payload or already in the database). Unresolvable edges are skipped with a warning.
- `category` must be one of: `SYSTEM`, `REGION`, `ORGAN`, `ORGAN_PART`, `TISSUE`, `CELL`, `SUBSTANCE`, `JOINT`, `OTHER`.
- `relation_type` must be one of: `PART_OF`, `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`, `SUPPLIED_BY`, `CONTINUOUS_WITH`.
- `standard_system` is optional and must be one of: `loinc`, `snomed`, `custom`.
- `display` is optional JSONB metadata. It holds body-map rendering hints under `display.map.markers`, **keyed by figure slug** (e.g. `man-front`, `woman-back` — slugs of rows in the `anatomy_figures` table). Each marker uses **normalized** coordinates (`nx`, `ny`, `nr` in the 0–1 range) relative to that figure's own viewBox. Absolute pixel coordinates are not used — positions are figure-independent and resolved at render time. See **§6.6**.

### 6.4 AI-Driven Graph Expansion

The system includes an AI orchestrator for on-demand anatomy graph generation. System admins can ask the AI chatbot to generate a detailed anatomical sub-graph (e.g., "Generate the anatomy of the cardiovascular system in detail"). The AI uses the `propose_anatomy_graph_generation` tool, which creates a HITL (human-in-the-loop) review card. Upon user confirmation, the AI generates the graph nodes and edges, validates them, and imports them via the same JSON import pipeline.

The AI task type `define_anatomy_graph` can also be called directly via `POST /api/v1/ai-assistance/assist` with a `user_input` describing the target structure.

### 6.5 Adding New Seed Nodes

To extend the base anatomy catalog that ships with the application:

1. Edit `backend/data/seeds/anatomy_base.json`.
2. Add new nodes to the `nodes` array (ensure unique `slug` values).
3. Add new edges to the `edges` array (ensure `source_slug` and `target_slug` reference existing slugs).
4. Restart the application (or run the manual seeding command above). The upsert logic will add new nodes and edges without affecting existing data.

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


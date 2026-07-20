# Taxonomy & Knowledge Graph

> The unified, multi-kind medical vocabulary that classifies every domain
> entity in the platform — specialties, examination/event/biomarker/anatomy/
> document categories, biomarker panels, medication classes, diseases, body
> systems, and more — plus a typed graph of relationships between them.

The taxonomy replaces the scattered single-purpose category tables
(`examination_categories`, `clinical_event_categories`, `biomarker_groups`)
and free-text `category`/`specialty` columns that existed before. One
concept can carry several domain tags, so "Blood Laboratory" is a single
node that is simultaneously an examination category, a biomarker class, and
a document category.

## Data model (`backend/app/models/concept_model.py`)

### `concepts` — the vocabulary nodes
Each row is one controlled-vocabulary term. Entity tables reference a
concept via a direct FK (single-valued classification) or via a
`concept_edges` row (M:N grouping).

| Column | Purpose |
|---|---|
| `slug` | Kebab-case identifier, **globally unique per tenant** |
| `name` | Display name |
| `primary_kind` | Denormalized mirror of one kind tag — for cheap single-badge rendering / coloring |
| `parent_id` | Self-FK for hierarchical concepts (e.g. an ATC class under its parent class) |
| `coding_system` / `code` | External terminology binding (`loinc`, `snomed`, `atc`, `icd10`, …) — `coding_system` is a free string, not an enum |
| `aliases` | JSONB array of synonyms (drives AI/OCR matching + search) |
| `icon` / `color` | UI rendering hints (Lucide icon config + hex color) |
| `status` | `draft` / `active` / `retired` (mirrors FHIR CodeSystem concept status) |
| `tenant_id` | NULL for global/seeded canonical rows; set for tenant-private overrides |

Unique partial index: `(slug, COALESCE(tenant_id, sentinel))` — global rows
share the same sentinel so duplicate global slugs collide correctly
(Postgres treats NULLs as distinct under UNIQUE).

### `concept_kind_tags` — the multi-kind join table
A concept's domain membership lives here, not on `concepts` itself. One
concept → many tags. The `(concept_id, kind)` pair is unique; cascade-deletes
with the parent concept.

```python
ConceptKindTag(concept_id=..., kind=ConceptKind.BIOMARKER_CLASS)
```

The `conceptkind` PG enum type is shared (no separate type per table).
`Concept.kinds` is a selectin-loaded convenience property returning the tag
values as strings.

### `concept_edges` — the typed polymorphic graph
Directed edges between two concepts, or between a domain entity and a
concept. Endpoints are **polymorphic** (`src_type`/`dst_type` tag the table
the UUID refers to) — there is no cross-table FK; referential integrity is a
service-layer concern.

| Aspect | Values |
|---|---|
| `relation` | `MEMBER_OF`, `HAS_SPECIALTY`, `CLASSIFIED_AS`, `EXAMINES`, `PERFORMS`, `ORDERS`, `LOCATED_IN`, `PART_OF`, `TREATS`, `INDICATES`, `PREVENTS`, `CONTRAINDICATES`, `CORRELATES_WITH`, `CAUSED_BY`, `MONITORS`, `RISK_OF`, `SCREENS_FOR`, `BRANCH_OF`, `DRAINS_INTO`, `ARTICULATES_WITH`, `INNERVATED_BY`, `SUPPLIED_BY`, `CONTINUOUS_WITH` |
| `source` | `seed` / `integration` / `ai` / `manual` (drives curated-wins conflict resolution) |
| `status` | `approved` / `proposed` / `rejected` — **only `approved` counts for graph queries**; `proposed` rows are HITL-pending (AI suggestions) |

Typical edges: a specialty `EXAMINES` a body system; a specialty `PERFORMS`
an examination category; a biomarker `MEMBER_OF` a biomarker panel; an ATC
medication class `PART_OF` its parent ATC class.

## The 16 ConceptKind domains (`backend/app/models/enums.py`)

| Kind | Example concepts |
|---|---|
| `specialty` | Cardiology, Neurology |
| `examination_category` | Blood Laboratory, Imaging & Radiology |
| `event_category` | Clinical event groupings (see [CLINICAL_EVENTS.md](CLINICAL_EVENTS.md)) |
| `biomarker_class` | Blood Laboratory, Vital Signs (shared with exam categories) |
| `biomarker_panel` | Lipid Panel, Complete Blood Count |
| `anatomy_class` | Anatomy region/organ/tissue groupings |
| `vaccine_class` | Vaccine families |
| `medication_class` | WHO ATC drug classes (`atc-c`, `atc-n`, …) |
| `document_category` | Laboratory Tests, Imaging (shared with exam categories) |
| `disease` | Diagnoses |
| `body_system` | Cardiovascular System, Nervous System |
| `procedure` | Procedures |
| `lifestyle` / `factor` / `symptom` | Recommendation-engine leaves |
| `organ` | Anatomical organs |

## Multi-kind concepts

The same medical term often belongs to several domains. "Blood Laboratory"
is an examination category **and** a biomarker class **and** a document
category. Before the consolidation these were three separate rows with no
link; now they are **one concept row** carrying three `concept_kind_tags`.

- The unique constraint is on `(slug, tenant)` — **not** `(kind, slug, tenant)`.
- `primary_kind` is a denormalized mirror of one tag for display ordering and
  the common "show one badge" case; it is kept in sync by the service layer.
- Querying "all biomarker classes" filters via the join table
  (`concepts_with_kind(ConceptKind.BIOMARKER_CLASS)` helper in
  `concept_service.py`), not via a column on `concepts`.

> **Note on same-name, different-concept:** "Cardiovascular System" the
> **body system** (`slug: cardiovascular-system`) and "Cardiovascular System"
> the **ATC drug class** (`slug: atc-c`, code `C`) are intentionally two
> separate concepts — one is anatomy, the other pharmacology. Link them with
> a `CORRELATES_WITH` edge rather than merging.

## Service layer (`backend/app/services/concept_service.py`)

`ConceptService` enforces tenancy, RBAC, and the soft-delete-with-retire
lifecycle:

- **RBAC**: `SYSTEM_ADMIN` may write **global** concepts/edges; `ADMIN`/
  `MANAGER` may manage **tenant-scoped** rows; `USER` is read-only.
- **Tenancy**: reads apply `or_(tenant_id == caller, tenant_id.is_(None))` so
  global canonical rows are visible to every tenant.
- **Lifecycle**: a concept with active edges is `retired` (not hard-deleted)
  to preserve graph integrity; a truly orphaned concept is soft-deleted.
- **Edges**: AI-proposed edges land as `status=PROPOSED` and are invisible to
  graph queries until approved.

`resolve_concept_by_slug(db, slug, kind?, tenant_id?)` bridges legacy
free-text / enum category strings to the unified table. Slug is now globally
unique per tenant, so the `kind` argument is optional (a safety filter).
`biomarker_category_to_concept_slug("blood_laboratory")` → `"blood-laboratory"`
(the legacy `-class` suffix is gone post-merge).

## API endpoints (`backend/app/api/v1/endpoints/concepts.py`)

All under `/api/v1`, standard JWT auth, tenancy-scoped.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/concepts?kind=&parent_id=&include_retired=&limit=&offset=` | List (filter by kind tag, parent, status) |
| `GET` | `/concepts/search?q=&kind=&limit=` | Hybrid search (trigram + FTS + RRF) over name/slug/description/aliases |
| `POST` | `/concepts` | Create — body accepts `kinds: [...]` (or legacy single `kind`) |
| `GET` | `/concepts/{id}` | Fetch one |
| `PUT` | `/concepts/{id}` | Update — `kinds` replaces the full tag set (≥1 required) |
| `DELETE` | `/concepts/{id}` | Soft-delete (or retire if referenced) |
| `POST` | `/concepts/{id}/restore` | Reverse a retire/soft-delete (status → active, clears `deleted_at`) |
| `GET` | `/concepts/{id}/neighbors?relation=&include_proposed=` | One-hop graph traversal |
| `GET` | `/concept-edges?src_type=&src_id=&...` | List edges |
| `POST` | `/concept-edges` | Create a typed edge |
| `DELETE` | `/concept-edges/{id}` | Hard-delete an edge |

The response shape carries `kinds: List[str]` and `primary_kind: str | null`
(not a single `kind`). The create endpoint accepts either `kinds: [...]`
(preferred) or a legacy single `kind: "..."` for backward compatibility.

> **Sole write authority:** `ConceptService` is the only concept write path —
> it enforces audit logging, the retire/restore lifecycle, and RBAC. The
> `ConceptCatalogAdapter` (`/catalogs/concept`) is **read-only**: `POST`/
> `PUT`/`DELETE` return `405` (use the `/concepts` endpoints above instead).
> `GET /catalogs/graph` returns the whole cross-catalog ontology graph
> (rootless) with `types`, `kind`, `include_isolated`, and `limit` filters.

## Catalogs workspace UI (`/catalogs`, SYSTEM_ADMIN for writes)

`TaxonomyManager.tsx` is **deleted**. Concept CRUD now lives in the
unified **Catalogs workspace** (`CatalogWorkspace`) at `/catalogs?type=concept`:

- **Concept form** (`ConceptForm`): multi-kind chips (`KindChips`), a
  parent-concept picker, a searchable **IconPicker**, coding
  (`coding_system` + `code`), aliases, and status (`draft` / `active` /
  `retired`).
- **List | Graph toggle** available for all catalog types — not just concepts.
- **`CatalogOntologyGraph`** renders the cross-catalog graph with
  type/kind filters, depth-limited BFS traversal, and PNG export.
- **Info tab (selected-item detail)** is a registry-driven, type-aware layout:
  each catalog type's fields are grouped into titled sections (Identity /
  Coding / Clinical / …) and rendered with specialized widgets — code badges
  with external lookups (LOINC/SNOMED/CVX/ATC/ICD-10/FMA), semantic chips
  (aliases, side effects, reactions, kinds), the biomarker stratified
  reference-range table + number-line, the vaccine dose schedule, and concept
  color/icon previews. Includes an in-preview field filter, a related-summary
  chip (jumps to Relations), a "needs attention" data-quality badge,
  Copy-JSON, and `e`/`r`/`h`/`Esc` keyboard shortcuts. Driven by
  `components/catalog/info/` over reusable `components/ui/` primitives.
- **"Export seeds" button** in the `CatalogToolbar` (SYSTEM_ADMIN) — calls
  `GET /api/v1/admin/seeds/export.zip` (see Seeds & import below).
- The legacy routes `/admin/system/taxonomy` and `/examinations/categories`
  **redirect** to `/catalogs?type=concept`.

## Seeds & import (`backend/data/seeds/`)

- `concepts.json` — the curated canonical vocabulary (~54 concepts). Each
  item carries a `kinds` array (the loader accepts legacy single-`kind` items
  too). Idempotent upsert by `(slug, tenant=global)`.
- `concept_edges.json` — the seed graph (specialty `EXAMINES` body system,
  specialty `PERFORMS` examination category, ATC `PART_OF` hierarchies).
- `_process_concepts` in `seed_service.py` syncs kind tags on upsert (add
  missing, remove unlisted) and is run at app startup.
- External catalog import (`CatalogImportService` /
  `ONTOLOGY_CATALOG.md`): a biomarker's legacy `category` string is
  translated to a concept via `biomarker_category_to_concept_slug`.

## Migrations

The taxonomy schema is part of the single consolidated baseline
(``alembic/versions/8ddb7ef7ca4d_consolidated_baseline.py``), which supersedes
the historical incremental chain. The net schema it establishes:

- ``concepts`` + ``concept_edges`` + the concept enums.
- The old scattered category tables consolidated into ``concepts``; entity
  tables carry ``class_concept_id`` / ``specialty_concept_id`` /
  ``category_concept_id`` FKs.
- The multi-kind model: ``kind`` lives on the ``concept_kind_tags`` join table
  with a ``primary_kind`` on ``concepts``; the slug unique index is
  per-tenant ``(slug, COALESCE(tenant_id, <sentinel>))``.
- Every classification FK standardized into ``concepts.id`` on the
  ``<role>_concept_id`` naming convention
  (``examinations.category_concept_id``, ``clinical_event_types.category_concept_id``,
  ``documents.category_concept_id``, ``anatomy_structures`` /
  ``biomarker_definitions.class_concept_id``, ``doctors.specialty_concept_id``,
  ``concepts.parent_id``).
- ``anatomy_relations`` unified into ``concept_edges``
  (``src_type='anatomy'``, ``dst_type='anatomy'``); the ``anatomy_relations``
  table is gone, and the 6 anatomy relation types (``BRANCH_OF``,
  ``DRAINS_INTO``, ``ARTICULATES_WITH``, ``INNERVATED_BY``, ``SUPPLIED_BY``,
  ``CONTINUOUS_WITH``) are part of ``ConceptRelationType``.

## Naming convention

Every domain-specific FK into `concepts.id` is named `<role>_concept_id` and
has a matching `<role>_concept` relationship declared with **explicit**
`foreign_keys=[...]` (so SQLAlchemy resolution never relies on single-FK
guesswork). The only sanctioned exceptions are the owned-child join row
`concept_kind_tags.concept_id` and the self-reference `concepts.parent_id`.

| Table | Column |
|-------|--------|
| `examinations` | `category_concept_id` |
| `clinical_event_types` | `category_concept_id` |
| `documents` | `category_concept_id` |
| `biomarker_definitions` | `class_concept_id` |
| `anatomy_structures` | `class_concept_id` |
| `medication_catalog` | `class_concept_id` |
| `allergy_catalog` | `class_concept_id` |
| `vaccine_catalog` | `class_concept_id` |
| `doctors` | `specialty_concept_id` |

This is pinned by `tests/test_concept_fk_naming_convention.py` (regex check +
explicit-`foreign_keys` resolution check + live-DB drift diff).

## Cross-Domain Edges & Traversal

`concept_edges` is the **single** cross-domain link system — not just
concept↔concept, but any entity↔any entity. The `EdgeEndpointType` enum (11
values: `concept`, `anatomy`, `biomarker`, `medication`, `allergy`,
`clinical_event_type`, `immunization`, `examination`, `doctor`, `observation`,
`document`) tags which table each UUID endpoint references. The
`ConceptRelationType` enum (25 values) carries the semantic:

| Relation | Example | Seeded in |
|----------|---------|-----------|
| `EXAMINES` | cardiology → heart (anatomy) | `concept_edges.json` |
| `IMAGES` | imaging-radiology → chest | `concept_edges.json` |
| `MEMBER_OF` | LDL → lipid-panel (concept) | `biomarker_panels.json` |
| `AFFECTS` | creatinine → left-kidney (anatomy) | `concept_edges.json` |
| `INDICATES` | glucose-fasting → endocrine-system | `concept_edges.json` |
| `MONITORS` | biomarker ↔ clinical_event_type | runtime (CRUD endpoint) |
| `TREATS` | medication → disease (Phase 6) | planned |
| `PREVENTS` | vaccine → disease (Phase 6) | planned |

### Endpoint resolver (`concept_endpoint_resolver.py`)

A registry of per-type resolver functions turns a bag of `(type, id)` pairs
into uniform display payloads `{type, id, label, icon, color, kind}` — so the
graph UI and recommendation engine don't each need to know every entity table.
**7 of 11** endpoint types have dedicated resolvers (concept, anatomy,
biomarker, examination, medication, allergy, clinical_event_type,
immunization); the rest fall back to a `"{type}:{id-prefix}"` label.

### Graph traversal (`catalog_graph_service.traverse()`)

`GET /catalogs/{type}/{id}/relations?depth=1-3&relation=&include_proposed=`
runs a **recursive CTE** over `concept_edges` — depth-bounded (1–5),
cycle-safe (edge-id `path` array), tenant-scoped, with optional
relation-whitelist and proposed-edge filter. Returns `{start, nodes, edges}`
with all endpoints resolved to display payloads. This powers the headline
cross-catalog query: "which organ does this biomarker affect? → what diseases
affect that organ? → what treats them?"

### Retired legacy link tables

The `biomarker_relationships` (biomarker↔biomarker) and
`biomarker_event_correlations` (biomarker↔clinical_event_type) tables were
dropped in Phase 3 (migration `c4d5e6f7a8b9`). Their semantics now live in
`concept_edges`: biomarker↔biomarker → `CORRELATES_WITH`; biomarker↔event-type
→ `MONITORS` (with `correlation_type`/`description` on the edge's `properties`
JSONB). The CRUD endpoints (`POST/GET/DELETE /clinical-events/types/{id}/
biomarkers`) and the `ClinicalEventEngine` recommended-biomarker insight were
rewritten to query `concept_edges`.

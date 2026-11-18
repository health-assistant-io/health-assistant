# Seeding & Demos

Creating demo data is critical for E2E tests, documentation screenshots, and local development. This project follows specific best practices to ensure seeding is idempotent, deterministic, and maintainable.

## 0. The Seed Pipeline (`SeedService.seed_all`)

Every application startup runs a single ordered, idempotent seed pipeline against the dev/prod DB (`backend/app/services/seed/` — the entity-family modules behind `SeedService`). Content flows through the **bundled seed pack** (`backend/data/seeds` wrapped as a pack — manifest + per-file sha256 pins): `seed_all` parses the pack once and dispatches each stage's parsed items to its seeder. The stage order is declared in `_SEED_STAGE_NAMES` so dependencies land first:

| # | Stage | Pack file | Depends on |
|---|---|---|---|
| 1 | `retention_policies` | — (app-only) | **FIRST** — the retention windows every lifecycle job reads; seeded defaults, insert-missing only |
| 2 | `proposal_kinds` | — (app-only) | **FIRST** — the HITL proposal-kind vocabulary from the family contract |
| 3 | `ai_task_defs` | — (app-only) | **FIRST** — the AI task vocabulary from the TaskDef registry |
| 4 | `concepts` | `concepts.json` | **FIRST** — `anatomy_class`/`biomarker_class`/… are referenced by `body_parts` (class_concept_slug→id), `default_catalog` (biomarker_class), `concept_edges`, and `diseases`. Must run before any stage that resolves a concept. |
| 5 | `diseases` | `diseases.json` | concepts (disease concepts — kind=disease, ICD-10) |
| 6 | `medications` | `medications.json` | — (standalone) |
| 7 | `vaccines` (rows + `PREVENTS` edges) | `vaccines.json` | diseases (the item's `target_diseases` refs resolve to disease concepts **by identity** and materialize as approved `PREVENTS` `concept_edges` rows) |
| 8 | `clinical_event_types` | `clinical_event_types.json` | — (standalone) |
| 9 | `allergies` | `allergies.json` | — (standalone) |
| 10 | `body_parts` (anatomy structures) | `anatomy_structures.json` | concepts (resolves `anatomy_class`) |
| 11 | `anatomy_figures` | — (binary side inputs, `anatomy_figures/*.webp` — bundled-only, never pack items in v1) | body_parts |
| 12 | `biomarker_states` (STATE biomarker picklists) | `biomarker_states.json` | — (standalone, universal; no deps) |
| 13 | `default_catalog` (units + biomarker definitions) | `default_catalog.json` | concepts (biomarker_class) |
| 14 | `concept_edges` (incl. concept→anatomy, anatomy→anatomy, biomarker endpoints) | `concept_edges.json` | concepts + diseases + body_parts + vaccines + `default_catalog` (the file's `biomarker:` endpoints resolve against biomarker definitions) |
| 15 | `biomarker_panels` (MEMBER_OF edges) | `biomarker_panels.json` | concepts + default_catalog |

The OMB race/ethnicity/language picklist (`omb_race_ethnicity.json`) is a pack item too — it seeds no rows; consumers (the setup wizard) read it **through the pack path** (`app.core.seed_pack.bundled.omb_picklist`), never as a raw file.

**Conventions:**
- **Envelope** — every pack item file is `{ "metadata": {...}, "items": [...] }` (the default catalog is `{metadata, units[], biomarkers[]}`; the OMB picklist keeps its `races[]/ethnicities[]/languages[]` lists). The parser normalizes every stage to its collections, so seeders always consume lists. (The legacy `anatomy_base.json` was split into `anatomy_structures.json` + `anatomy_relations.json`; the latter is now **deleted** — anatomy hierarchy edges live in `concept_edges.json` with `src_type=anatomy, dst_type=anatomy`. `biomarker_panels.json` migrated from a `{panel: [slugs]}` map to the standard items list.)
- **Transaction law** — every seeder takes a **required session** and never opens its own or commits: the caller owns the transaction (boot = one transaction in `main.py`, CLIs commit after their stage, the hard reseed commits once, the pack applier commits per pack stage). A rolled-back stage leaves nothing behind.
- **Identity** — every curated item carries a mandatory, immutable qualified `seed_key` (`<type>:<kebab-key>`; see [the seed identity section](#the-identity-of-a-catalog-row-seeder-rules)). Seeders upsert **by deterministic id, then `seed_key`** — never by name or slug.
- **Edges resolve by identity, with deterministic ids** — `concept_edges.json` keeps readable endpoint refs (`src_slug`/`dst_slug` + optional `src_type`/`dst_type`); each ref resolves to the endpoint row **by identity** (the uuid5 of its qualified seed key — `concept:<slug>`, `medication:<kebab-key>`, `anatomy:<slug>`, `biomarker:<slug>`, `vaccine:<slug>` — then the row's `seed_key` column), never by name. Panel memberships resolve the same way: `panel_slug` as `concept:<panel>` (a `biomarker_panel` concept) and `biomarker_slug` as `biomarker:<slug>` — always `tenant_id IS NULL`, so a tenant row sharing a member slug is never bound into a curated edge. Every curated edge gets the deterministic id `uuid5(SEED_KEY_NAMESPACE, "concept-edge:<src>:<key>/<dst>:<key>/<RELATION>")`, so a reseed is edge-id-stable, and **only an `APPROVED` tuple short-circuits a re-run** — a pending/rejected proposal between the same endpoints never suppresses the curated seed edge.
- **Vaccine → disease links are graph data** — `vaccines.json` carries the readable `target_diseases` list (sorted disease-concept refs); the vaccine seeder materializes it as approved `PREVENTS` edges (`source=seed`) with uuid5 ids (`vaccine-prevents:<vaccine-key>/<disease-key>`). The JSONB column is gone (migration `0012_vaccine_target_edges`); the graph is the single source of truth. Unresolvable refs are skipped with a warning, never guessed by name.
- **Stats contract** — every `seed_*` returns `{added, updated, unchanged, skipped, errors}`. `unchanged` counts rows whose values already matched the JSON (a second run reports all-`unchanged`, zero writes); `seed_default_catalog` additionally carries a `details: {units_added, units_updated, units_unchanged, biomarkers_added, biomarkers_updated, biomarkers_unchanged}` sub-dict, and `seed_vaccines` a `details: {edges: {added, unchanged, skipped}}` breakdown (rows and edges are counted together in the top-level buckets).

- **Idempotency + reconciliation** — re-runs upsert by identity and reconcile existing rows to the JSON: only fields that really differ are written. Editing a concept's `kinds` array and restarting will diff its kind tags into place; nothing is ever deleted by a seed (only `reset-curated` deletes, see below).
- **Boot = the bundled pack apply (digest-gated)** — `main.py` no longer re-seeds blindly on every start: the bundled pack is a **trusted, auto-approved source** (`seed_sources` kind `bundled`, priority 0), its digest is re-checked on boot, and the apply runs **only when the digest changed** (or nothing applied it yet) through the applier below — audited, with per-stage transactions. A fresh DB works before any admin exists (no approval, no user needed). The failure policy is unchanged `_abort_or_warn`: fail-soft in dev, abort boot in prod.

### 0b. The pack lane: planner, applier, precedence, provenance

Every pack — the bundled one included — flows through the same lane (`app/services/seed/planner.py` + `applier.py`):

**Planner** (read-only diff, stored as JSONB in `seed_import_runs.plan`; deterministic, no timestamps):

- **Diff by identity** — items are keyed by the immutable `seed_key`; actions are `create` / `update` / `noop` (content-hash comparison against `seed_item_provenance.content_hash`), `conflict`, `skip`, `override`. The content hash is canonical JSON of the item minus its identity fields (`id`, `seed_key`) — the same function everywhere, so cross-source comparisons are exact.
- **Precedence** — priority-ascending: bundled (0) < curated remote (100) < tenant overlay (200+; the pack validator pins system packs to 0–199 and tenant packs to 200–999). A **higher** priority takes the key over and the displaced provider is recorded on the winner's entry (`override`); an **equal** priority with *different* content is a typed `conflict` that **blocks the whole apply** (run status `conflicted`, nothing written); equal priority with identical content is a peer `noop`; a **lower** priority plans `skip` and is recorded as the loser.
- **User edits win** — every human write path (generic/domain catalog writes, anatomy import, the atlas marker PATCH, HITL proposal creates) flips `seed_item_provenance.user_modified` through one service hook (`app/services/seed/provenance.py::mark_user_modified`). A flagged row is never auto-updated: differing pack content plans `skip` + `update_available` (adopting it is an explicit action — the sources admin phase adds the door). Seed/applier writes never touch the flag.
- **Warnings** — homoglyph / near-duplicate names (NFKD + casefold equality under different keys, in-pack and against existing rows) and ambiguous `replaces` pairings land in `plan.warnings`.
- **Retirement** — items the pack drops are retired (provenance `state=retired`; concept rows also `status=retired` — the row itself is **never deleted**) only when no other enabled, approved source still provides the key (provenance + successful run history) and no patient/user data references the row (FK discovery against the database catalog; curated/seed-internal referrers never block).
- **`replaces`** — manifest entries are key *bodies* (`replaced-old`, the contract pattern forbids `:`); each resolves against the pack's own key spaces to exactly one existing row. The old row retires, curated edges **in the pack's own lane** repoint to the replacement row (system edges for a system pack, the tenant overlay's edges for a tenant pack — an overlay repoint never rewrites the system graph), and patient `*_catalog_id` FKs are deliberately untouched. An ambiguous pairing (zero or several candidates) downgrades to retire-only with a loud warning.

**Applier** (the execution half, one `seed_import_runs` row per version):

- **Advisory lock** — a Postgres session-level lock held for the whole apply on a dedicated connection: one global key for system applies, per-source keys for tenant applies; acquired in sorted order so concurrent applies cannot deadlock.
- **Run-status guard** — an apply refuses while a recent `running` run exists for the same source; a stale one (crashed process, > 6 h) is marked failed and superseded.
- **Order** — priority-ascending; **every pack's item/side stages first (declared stage order), then every pack's ref/edge stages** (`concept_edges`, `biomarker_panels`). One transaction per pack stage: a failed stage rolls back completely, the run ends `failed`, and the source's `current_version_id` does **not** move (other packs in the same apply still run).
- **Plan-enforced writes** — only entries whose action writes (`create`/`update`/`override`) reach the seeders; `skip`/`conflict`/`noop` items never touch the rows (the freeze and the priority losers are enforced here, not trusted to the seeder).
- **Deferred references are first-class** — an unresolved cross-item reference (a concept parent, an edge endpoint, a panel member, a vaccine's target disease) becomes a `seed_deferred_refs` row (deduplicated per version; edges use their deterministic identity string as the tracking key) and every pending row is **retried on any new approval**; a ref that resolves flips to `landed` — never silently skipped.
- **History + provenance + audit** — every plan entry becomes a `seed_import_run_items` row (with the conflict payload of a loser/override), applied items update `seed_item_provenance`, seeder item errors are recorded as run items (per-item attribution, no swallowed failures), and the pointer advance is audited fail-closed (`action=seed.apply`). Search indexes are expression GIN (trigram + FTS) maintained by Postgres on write — there is no separate vector to refresh.
- **Versions are digest-pinned** — `UNIQUE(source_id, digest)`; approval is the anchor; revocation/rollback re-applies the previous approved version and **restores** what its successor retired (provenance back to `active`, concepts back to `status=active`).

The dev loop is unchanged: edit `backend/data/seeds/*.json` → restart — the digest changes, the next boot applies the new bundled version (audited, visible in `seed_import_runs`). `scripts/seed_pack.py pack|validate|digest` builds and checks packs for export/CI.

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

**The sanctioned pack lane** is the one place a zip-like payload is
welcomed: a **seed pack** uploaded to a `file` source is parsed and validated,
never executed or imported as code — the manifest pins every item file's
sha256, the coverage matrix rejects unknown item types, and nothing applies
until an admin approves that exact digest (§4). Curated content from another
instance travels as the **canonical seed pack** (§7): hashed, reviewable, and
round-trip-identical through the source lane.

## 2. Deterministic Execution

Seeds must be **idempotent**. Running `python3 scripts/seed_demo.py` multiple times must not create duplicate tenants, users, or patients.
- Use `scalar_one_or_none()` to check for existing records (e.g., matching a fixed `slug` or `email`).
- Check for existing data (e.g., `Patient.mrn` or `Observation.subject`) before bulk-inserting.

## 3. Date Freezing for Visual Regression

When generating data for screenshots (e.g., `capture_ui.sh`), dates must be relative to a "frozen" present.
In this project, the UI capture runner (`scripts/ui-capture/capture.mjs`) freezes the browser clock (e.g., to `2026-06-15`).
Your seed script should generate FHIR resources with absolute dates relative to that same frozen point to ensure charts and relative times ("2 days ago") render identically across runs.

The seed also provisions a **mock chat AI** (provider_type `mock` — a scripted model answering from real tool results, no API key) so the AI chat demo and the `ai-chat` screenshot work after every reset. Opt out with `HA_AI_MOCK=0`; a provider configured via the UI outranks it.

## 4. The Seed Data Structure

A clinical seed should use the internal `ImportService.restore_fhir_bundle` and include:
1. **Tenant & User**: A dedicated demo tenant and at least one Admin user.
2. **Patients**: At least one primary patient with a predictable MRN.
3. **Biomarkers**: `Observation` resources mapped to standard LOINC codes (the system will auto-resolve these).
4. **Medications**: `MedicationStatement` resources.
5. **Allergies**: `AllergyIntolerance` resources.
6. **Examinations**: Sideloaded via `restore_sidecar("examinations.json", ...)` to provide clinical context notes.

### Seed identity (the natural key)

Catalog and seed rows follow one identity model (schema in
migrations `0010_catalog_identity` + `0011_instance_catalog_fks`):

- Ownership is exactly `scope` + `tenant_id`: curated rows are `system` rows
  (`tenant_id IS NULL`), tenant packs write `tenant` rows. There is no
  `is_custom` flag to set in a payload (the retired field is a 422).
- The **UUID is the identity**; curated rows derive it deterministically
  (`uuid5` from the key below), user-created rows get random UUIDs.
- The **`seed_key` is the natural key**: immutable, `kebab-or-slash` form,
  qualified `<type>:<kebab-key>` when written as one string — see
  `app/core/catalog_identity.py` (`validate_seed_key`, `seed_key_uuid`), unique
  **per tenant scope** (`COALESCE(tenant_id, sentinel)` — a tenant
  overlay may reuse a system key at a higher precedence). The sentinel is the
  shared all-zero UUID (`catalog_identity.NULL_TENANT_SENTINEL`, the same
  literal `0001_baseline` uses), and the per-scope uniqueness lives in the
  `ix_<table>_seed_key_tenant` partial expression indexes on
  `medication_catalog`, `allergy_catalog`, `vaccine_catalog`,
  `biomarker_definitions` and `concepts` (where `seed_key IS NOT NULL` —
  user-created rows stay outside the curated identity).
- **Standard codes are attributes** (RxNorm/ATC, SNOMED, CVX, LOINC), never
  the identity. `medication_catalog` and `allergy_catalog` carry open
  `coding_system` + `code` columns with their own per-scope unique
  (`ix_<table>_code_tenant`); vaccine CVX and biomarker LOINC codes stay
  where they are.
- **`slug` is display-only**: server-derived, per-tenant unique, never
  referenced by other tables and never a seed join key. The vaccine slug is
  per-tenant unique (`ix_vaccine_catalog_slug_tenant`).
- **Tenancy scope is DB law**: every catalog table (and `concepts`) carries
  CHECK constraints — `scope='system' ⇒ tenant_id IS NULL`,
  `scope IN ('tenant','user') ⇒ tenant_id IS NOT NULL`,
  `scope='user' ⇒ created_by IS NOT NULL` — and `concept_edges` allows one
  approved edge per tenant scope + endpoints + relation
  (`ix_concept_edges_approved_unique`); proposed/rejected rows may repeat.
- **Instance → catalog is FK + snapshot**: `fhir_medications`,
  `fhir_allergy_intolerances` and `patient_immunizations` hold a nullable
  `*_catalog_id` (`ON DELETE SET NULL`) next to their JSONB snapshot, which
  stays the point-in-time display record.

Seeders and imports upsert **by id, then `seed_key` — never by name or
slug**, so renames do not fork rows and references do not dangle.

### The identity of a catalog row (seeder rules)

Every curated seed item carries its `seed_key` **in the JSON** (mandatory,
validated at load — an item without one fails that item loudly). The shared
helper lives in `backend/app/services/seed/identity.py`
(`require_seed_key` → `find_identity_row` → `apply_values` /
`upsert_by_identity`); every seeder goes through it:

- **Row id** = `uuid5(SEED_KEY_NAMESPACE, "<type>:<seed_key>")`
  (`catalog_identity.seed_key_uuid`) — stable across reseeds and machines.
  Tables without a `seed_key` column (`units`, `biomarker_states`,
  `anatomy_structures`, `clinical_event_types`, panel-membership edges)
  still carry the key in their JSON and get the same deterministic id —
  identity lives in the id.
- **Lookup order**: by id first, then by `seed_key` — always filtered
  `tenant_id IS NULL`. A tenant overlay sharing the seed key (or the name)
  is invisible to a seed run and is never updated or deleted.
- **Updates** write only fields that really differ (a fully-matching run
  reports `unchanged` and touches nothing); `created_at`/`created_by` are
  set at creation only; JSONB fields are `flag_modified`-marked on change.
- **Codes are attributes**: a medication/allergy seed updates
  `coding_system`/`code` only when the item provides them — absent codes are
  left alone, and no code is ever fabricated (seed items carry real ATC
  codes or none).
- **Explicit `id`** in a seed item wins over the derived id at creation
  (pack/import use). An id that already belongs to a **different** seed key
  is rejected (`SeedIdentityConflict`), never silently adopted; an existing
  row's id is never retargeted.

### Hard reseed of the curated system catalogs

`reset_curated_system_rows` (`backend/app/services/seed/reset.py`) deletes
**only curated system rows** — `tenant_id IS NULL` on every catalog table
(seed-provenance rows only for `concept_edges`) — and re-runs the pipeline,
recreating every row under its deterministic id. Tenant rows themselves are
never deleted, but the reset **refuses to run** (typed `CuratedResetBlocked`)
while any row that would *survive* it still references a deleted system row:
RESTRICT FKs (tenant event types on system category concepts, tenant
biomarker allowed-state rows on global states) would fail the delete
outright, and `SET NULL`/`CASCADE` FKs would silently null or destroy the
surviving references. Those are discovered generically from the database
catalog — every FK with `ON DELETE SET NULL`/`CASCADE` into a truncated
table is counted — so the covered shapes include instance rows linked to
their catalog rows (medication statements, allergy records, immunizations),
clinical-event anatomy links (CASCADE) and occurrences, tenant concept
children of system parents, tenant biomarkers and tenant reference ranges
pinning curated `units` rows, tenant notification rules on system
biomarkers, and any FK a future catalog adds. Only rows the reset deletes
itself are exempt: system catalog rows and the seed-recreated child tables
(`concept_kind_tags`, `biomarker_reference_ranges`,
`biomarker_allowed_states`), which cascade away with their truncated parent
— catalog churn, not data loss. The preflight names every blocking count in
the error. It is idempotent; re-running converges to the same ids.

```bash
cd backend && uv run python scripts/seed.py                # plain seed_all
cd backend && uv run python scripts/seed.py --reset-curated
```

`--reset-curated` is the migration path for installs created before the
identity model (their rows carry random ids / no `seed_key`): run it once —
it is safe because there are no users; system vocabulary is shipped data,
not user data. On an install **with tenant or instance references** into the
curated rows it refuses (see above) rather than silently breaking them.

### The seed pack (delivery unit)

A **seed pack** is one `manifest.json` plus hashed item files — the unit for
bundled, imported and exported seed data. The manifest carries `id`,
`schema_version`, `version`, `license`, `scope` (`system`/`tenant`),
`priority` (bundled 0 < curated remote 100 < tenant overlay 200+), optional
`replaces` (seed keys retired/repointed by a rename), and `item_files` with
per-file sha256 pins. Item files keep today's envelopes from
`backend/data/seeds/*.json`: `{metadata, items[]}` for the entity files,
`{metadata, units[], biomarkers[]}` for `default_catalog.json`, and the
OMB picklist's `races[]/ethnicities[]/languages[]`. Packs are **data only**:
parsed and validated, never executed.

**Coverage matrix** (`app/core/seed_pack/items.py` — the registry is the
whole truth). Pack items: concepts, diseases, medications, vaccines, clinical
event types, allergies, body parts (anatomy structures), biomarker states,
default catalog (units + biomarkers), concept edges, biomarker panels, and
the OMB picklist. **App-only stages** (`retention_policies`,
`proposal_kinds`, `ai_task_defs`) are code/contract vocabularies the app
seeds from its own registries — a pack declaring them is a typed
`APP_ONLY_ITEM_TYPE` error; an unrecognized item file is
`ITEM_TYPE_UNKNOWN`. **Binary anatomy figures are bundled-only in v1** (the
WebPs ride with the repo and are copied to `HA_UPLOAD_DIR`; the pack path
grammar admits `.json` only). The validator also enforces per-file and
whole-pack size caps, JSON depth/node caps, field-level rules (hex `color`,
trimmed printable `code`/`standard_code`, no control characters), per-key-
space `seed_key` duplicates (`DUPLICATE_SEED_KEY`), and
`min_app_version`/`schema_version` gates — the typed vocabulary is closed
(`ERROR_CODES`, fixture-pinned).

**Tenant capability matrix.** Tenant packs (`scope: tenant`) may only target
**tenant-ownable** types — medications, vaccines, allergies and clinical
event types; a global-only type (units, biomarker states, the system
taxonomy/concepts, anatomy, edges/panels, the OMB vocabulary) in a tenant
pack is rejected with `GLOBAL_ONLY_ITEM_TYPE`. System packs may target
everything.

The contract is app-owned: `contracts/seed-pack.schema.json` with executable
fixtures in `contracts/seed-pack.fixtures.json`. The CLI builds and
validates packs:

```bash
cd backend
uv run python scripts/seed_pack.py pack --out /tmp/bundled-pack   # bundled pack from data/seeds
uv run python scripts/seed_pack.py validate /tmp/bundled-pack
uv run python scripts/seed_pack.py digest /tmp/bundled-pack       # whole-pack digest
uv run python scripts/seed_pack.py digest data/seeds/medications.json  # a file pin
```

The bundled build is deterministic — raw byte copies, canonical manifest,
**no timestamps** — so the same seeds always produce the same manifest and
the same whole-pack digest (the digest the boot apply (Phase 9B) re-checks).

Remote packs (git / file / registry / integration sources) are
**digest-pinned and approved before apply**: a fetched version is stored by
its content digest (`seed_source_versions`, `UNIQUE(source_id, digest)`), an
admin approves that exact digest, and apply runs in priority order —
user-modified rows are never auto-updated ("update available" instead),
items a retiring pack drops are **retired, never deleted** (and only when no
other active approved source still provides them), and unresolved
cross-pack references are stored (`seed_deferred_refs`) and retried when a
new version lands instead of being skipped silently. The lane's schema
landed in migration `0015_seed_packs` (sources, digest-pinned versions,
plan/apply runs with per-item outcomes, per-row provenance, deferred refs).

### The source lane (program Phase 10): transport, approval, apply

**Sources.** `seed_sources` rows are `file | git | registry` origins (the
`bundled` kind is boot-only and cannot be created) with a scope (system /
tenant), a precedence `priority` inside the scope's band (system 0–199,
tenant 200–999) and, for remote kinds, an https-only `uri`, a pinned
`ref_spec` (branch/tag name or 40/64-hex commit) + `subpath` (git) and a
secret **slot** reference (`auth_secret_ref` — resolved from the OS keyring
(`health-assistant/seed-sources` service) with the
`HA_SEED_SOURCE_SECRET_<SLOT>` env fallback for server installs; a token is
never stored in a row, a log line or an error). A tenant `ADMIN` registers
tenant overlays for their own tenant; the system lane is `SYSTEM_ADMIN`-only;
cross-tenant ids are hidden `404`s.

**Transport** (the repo's first git/URL fetcher — `app/services/seed/
transport.py`, built on the vendored `polite_http` SSRF guard). Every remote
hop is resolve-then-connect with the checked address **pinned** into the
request (Host header + TLS SNI keep the real name), redirects are re-guarded
per hop and never followed into private ranges, `file://`/http/userinfo URLs
are structurally refused, loopback/private/numeric-literal targets are
blocked (trusted self-hosted names can be exempted via
`HA_SEED_SOURCE_ALLOWED_HOSTS`; `HA_SEED_SOURCE_ALLOW_PRIVATE` is the
fail-open hatch, default off), and fetches run under size and wall-clock
caps (`HA_SEED_FETCH_MAX_BYTES`, `HA_SEED_FETCH_TIMEOUT_SECONDS`). Git runs
the CLI shallow (`--depth 1`) with redirects disabled on both the clone and
the commit-sha fetch, auth riding the environment (never argv/URL); registry
bodies stream under the byte cap, answer conditional GETs (`If-None-Match` →
304 = "nothing new"; an empty 200 body is a missing pack, not a 304), and
credentials never follow a cross-host redirect hop. **A failed
fetch keeps every pin** and lands in `seed_sources.last_error`/
`last_check_at`. Remote sources sync on a 6-hour worker beat
(`sync-seed-sources`) plus the admin "sync now" door; **startup never
fetches** — the bundled pack applies from local content at boot.

**Uploads** (file sources) accept a full pack zip (`manifest.json` +
`items/*.json`, the `unpack_seeds_zip` entry rules: traversal, backslash,
absolute and non-pack entries are typed refusals, expansion capped) or a
single item-file JSON (the file's basename must be a registered pack item
filename; the manifest is synthesized carrying the source's scope/priority).
The request body is read under the upload cap before any parsing — an
oversized body is a typed `UPLOAD_TOO_LARGE`/413. The parsed digest lands as
a pending version; re-uploading the same content
is idempotent. Uploaded/fetched pack content is cached content-addressed
under `HA_UPLOAD_DIR/seed-packs/<source>/<digest>.zip` so **plan and apply
never need the network**.

**Coherence.** At registration the manifest's `scope`/`priority` are pinned
to the source row — a pack that disagrees is refused (`SCOPE_MISMATCH` /
`PRIORITY_MISMATCH`) and nothing registers. The planner and applier
afterwards trust the **source row's** priority alone, so the precedence
ladder can never drift from what was approved.

**Lifecycle.** `pending → approved / rejected`, `approved → revoked` — every
transition digest-bound and audited. Approval flips the status only; the
pointer advances on a successful apply. Revocation **re-applies the previous
approved version** (the applier's retirement-restore flips what the revoked
version had retired back to `active`) or leaves the source pointer-less. The
admin API (`/api/v1/admin/seed-sources` — see `docs/API.md`) carries sync,
upload, approve/reject/revoke, the plan preview (`GET /versions/{id}/plan` —
it records the plan-mode run, its one documented side effect), the apply door
(plan-only default, `{"apply": true}`
executes — also the explicit re-apply door for a PARTIAL bundled apply),
adopt ("update available" made explicit: clear `user_modified`, re-apply — a
failed re-apply is audited once with outcome `error` and surfaces as a typed
`ADOPT_APPLY_FAILED`; the freeze-clears are never un-audited state),
disable/enable, and retire (its items retire **only** where nothing else
provides them; rows are never deleted — the retirement holds the apply lane's
advisory lock, so it cannot interleave with a live apply).

**Tenant overlays.** A tenant pack applies into the tenant's own rows: row
ids are tenant-qualified (`uuid5(NS, "<tenant>/<type>:<key>")`) so a tenant
overlay may reuse a system `seed_key` (the per-tenant-scope
uniqueness), the tenant overlay wins over the system lane for that tenant,
another tenant's claim never enters a tenant pack's precedence decision,
equal-priority different content within one tenant is a typed conflict that
blocks the apply, a tenant vaccine's `PREVENTS` edges inherit its tenancy
with tenant-qualified edge ids, and deferred-reference retries are
scope-pinned (a tenant apply retries only its tenant's pending refs).

**Sources UI.** The lane's admin surface lives at
`/admin/system/seed-sources` (`SYSTEM_ADMIN`) and
`/admin/tenant/seed-sources` (tenant `ADMIN` for their own overlays),
permission-gated by `seed_source:read` / `seed_source:manage` /
`seed_source:approve`. It renders the sources table (kind, uri/ref/subpath,
pack id + version, digest, declared file count, priority, scope, enabled
state, last error / last check), the add-source wizard (file upload or
git/registry URL + secret slot, plus pinned ref + subpath for git, with the
scope's priority band defaults), the plan/diff preview (per-stage entries
with create/update/no-op/skip/
conflict/retire badges, warnings, `update available` rows and item errors),
approve / reject (with reason) / revoke, sync now, disable / retire, and the
per-source run history (status, stats, the recorded plan and per-item
outcomes). A pending digest always surfaces for review — even when an
approved version is currently applied. The canonical seed-pack export
(`Download seed pack`) is offered to `SYSTEM_ADMIN` only (the export route
requires `SEED_SOURCE_READ@SYSTEM`). "Update available" (a pack update
blocked by a user edit) is
surfaced per row in the plan preview with an explicit **Adopt updates**
action; a PARTIAL apply carries the **Re-apply** door (retrying exactly the
failed items — this is also the bundled lane's partial door).

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

Anatomy hierarchy **edges** are no longer a separate file — `anatomy_relations.json` is **deleted**; the edges now live in `concept_edges.json` (seeded at the `concept_edges` stage) with `src_type=anatomy, dst_type=anatomy`. The structures file contains **54 nodes** (major body systems, organs, regions, and joints with SNOMED codes), each carrying an `anatomy:<slug>` `seed_key`. The seeding is idempotent — nodes are upserted by their deterministic identity id (`uuid5` of the seed key), never by slug, and duplicate edges are skipped.

The startup log (emitted by `seed_all`) shows:
```
Seeding body_parts...
Seeded body_parts: {'added': 54, 'updated': 0, 'skipped': 0, 'errors': 0}
```

### 6.2 Manual Re-seed

To re-seed the base anatomy catalog manually (e.g., after editing the split seed files), call the service method directly against a running backend:

```bash
cd backend && PYTHONPATH=. python -c "
import asyncio
from app.core.database import AsyncSessionLocal
from app.services.seed.service import SeedService

async def main():
    async with AsyncSessionLocal() as session:
        print(await SeedService().seed_body_parts(session))
        await session.commit()

asyncio.run(main())
"
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

The system includes an end-to-end AI orchestrator for on-demand anatomy graph generation. System admins can ask the AI chatbot to generate a detailed anatomical sub-graph (e.g., "Generate the anatomy of the cardiovascular system in detail"). The AI uses the `propose_anatomy_graph_generation` tool, which creates a HITL (human-in-the-loop) review card; on confirmation the client generates the graph (nodes + edges) via the `define_anatomy_graph` assistance task, surfaces an editable node/edge review, and imports them through the same JSON pipeline as `POST /api/v1/anatomy/import` (SYSTEM_ADMIN-gated — the import writes to the **global** ontology shared across all people, so the modal warns the user about the global scope before commit).

The AI task type `define_anatomy_graph` can also be called directly via `POST /api/v1/ai-assistance/assist` with a `user_input` describing the target structure — this path returns `{nodes, edges}` for programmatic import without the chat card.

### 6.5 Adding New Seed Nodes

To extend the base anatomy catalog that ships with the application:

1. Edit `backend/data/seeds/anatomy_structures.json` (add nodes to `items`) and/or `backend/data/seeds/concept_edges.json` (add anatomy→anatomy edges to `items`, with `src_type=anatomy, dst_type=anatomy`).
2. Ensure node `slug` values are unique, and that edge `source_slug`/`target_slug` reference existing slugs.
3. Restart the application (or run the manual re-seed command in §6.2). The upsert logic will add new nodes and edges without affecting existing data.

### 6.6 Body Diagram Atlas & Organ Markers

The Anatomy Explorer renders a 2D human body and overlays organ markers so users can locate structures visually. The atlas is **fully DB-driven, image-based, and admin-manageable**:

- **`anatomy_figures` table** — each row is one view of one figure (e.g. `man-front`, `woman-back`) backed by a **WebP image** stored on disk under `HA_UPLOAD_DIR/anatomy_figures/`. No SVG markup or viewBox — the stored image IS the view. The four defaults ship as bundled WebP seeds (rasterized from the Wikimedia surface diagrams at 3×, CC BY-SA 3.0 — see `NOTICE`) and are copied to `HA_UPLOAD_DIR` at startup.
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
(`backend/app/services/seed/seed_export_service.py`) is the strict inverse of
`SeedService`: it reads the DB and emits the `{metadata, items}` envelope each
`seed_*` stage consumes, covering **every registered pack item file** —
concepts, diseases, vaccines, medications, allergies, clinical event types,
concept edges, anatomy structures, biomarker states, the default catalog,
biomarker panels, and the OMB picklist (which flows through the pack path and
exports as a normalized pass-through, since it has no table of its own).

The output is **canonical and deterministic**: sorted by `seed_key` (slug/name
fallback for rows outside the curated identity), fixed field order, only
meaningful fields, and a `metadata` block with **no timestamps** —
re-exporting an unchanged DB is a byte-level no-op, so a git diff means real
content changed.

### 7.0 The canonical pack (one format for export, CI and round-trip)

`SeedExportService.build_pack()` wraps the exported files as **one canonical
seed pack**: a `manifest.json` (id `exported-instance-seeds`,
system scope, priority 0, license `CC-BY-SA-4.0`) pinning every
`items/*.json` by sha256. The pack validates through the same contract as the
bundled one (`contracts/seed-pack.schema.json`), the whole-pack digest is
stable across builds, and the download ZIP is fully deterministic (fixed
entry order, fixed archive timestamps). Because it is an ordinary pack, an
export can be **uploaded back to a `file` source and re-applied with zero
writes** — the round trip the source lane was built for. A template-tenant
export is also system-scoped (its rows are curator input for the canonical
set; a tenant pack could not carry the global-only item files).

### 7.1 Identity model

**Every item exports its `id`; curated rows export their immutable
`seed_key`; codes are attributes.** Seeders upsert by id, then `seed_key` —
never by name or slug — so **exporting and re-ingesting is a zero-write round
trip** (pinned by `test_seed_edges_export.py`). Cross-references stay in
their readable form (`parent_slug`, `src_slug`/`dst_slug`, `panel_slug`,
`class_concept_slug`) but resolve **by identity** on import. Two rows carry
no `seed_key` column (the identity lives in the id alone): anatomy structures
and clinical event types export their **mechanical** key (`anatomy:<slug>` /
`clinical-event-type:<slug>`, emitted when the row's id matches the derived
uuid5); units round-trip by their symbol (the immutable natural key) plus the
`id` — and a **fresh install** built from the export recreates every unit
under the exported `id`, so re-exports are stable instead of drifting to new
random ids. `coding_system` + `code` are emitted where present.

Vaccine → disease links are graph data: `vaccines.json` exports the readable
`target_diseases` list **derived from the approved `PREVENTS` edges** (the
inverse of the vaccine seeder), and `concept_edges.json` excludes exactly
those edges — one owner per dataset.

### 7.2 Three delivery surfaces (same service underneath)

**UI download (SYSTEM_ADMIN)** — the **Catalogs workspace** toolbar
(`/catalogs`) has an **Export seeds** button that calls
`GET /api/v1/admin/seeds/export.zip` and saves the **canonical pack ZIP**
(`health-assistant-seed-pack.zip`: `manifest.json` + `items/*.json`). The
download is **read-only** — the server never writes its own `data/seeds/`.

**CLI (curator on the same machine as the DB)**:
```bash
cd backend && # (uv manages the environment) && export PYTHONPATH=.:../
python scripts/export_seeds.py --dry-run    # preview counts, no writes
python scripts/export_seeds.py              # global taxonomy -> data/seeds
python scripts/export_seeds.py --source TENANT_ID   # a template tenant
python scripts/export_seeds.py --out /tmp/seeds     # custom output dir
```
The write is safe: files stage to `.export-staging/`, existing files back up
to `.backup-<timestamp>/`, then atomic write.

**Dev-side unpack helper** — for the transfer workflow (instance on another
machine): download the pack ZIP via the UI, copy it to your dev machine, then:
```bash
python scripts/unpack_seeds_zip.py path/to/health-assistant-seed-pack.zip
```
Every `items/*` entry is verified against its manifest sha256 pin **before**
anything is written (a tampered or truncated archive never lands), existing
`data/seeds/*.json` back up to `.backup-<timestamp>/`, and the `items/`
prefix is stripped into the flat seeds tree. A hand-rolled flat ZIP of
registered seed filenames is also accepted; unknown or path-traversal
entries are refused in both shapes.

### 7.3 The transfer workflow (instance on another machine)

1. On the running instance: Catalogs workspace → **Export seeds** → `health-assistant-seed-pack.zip`.
2. Transfer the ZIP to your dev machine (download, scp, USB).
3. `python scripts/unpack_seeds_zip.py health-assistant-seed-pack.zip` (verify pins + backup + extract).
4. `git diff data/seeds/` → review → commit. The next release ships the new seeds.

The same pack can travel the **source lane** instead of the seeds tree:
upload it to a `file` source (§4), review the plan, approve the digest and
apply — full provenance, no git required.

### 7.4 Drift gate (CI)

`scripts/check-seed-pack.sh` (wired into CI after the backend suite) proves
the checked-in tree is publishable:
- **P1** the bundled pack builds and validates against the pack contract
  (an unregistered JSON file or a missing item file fails here);
- **P2** two builds of the same tree are byte-identical and
  `seed_pack.py pack --check` is green — the digest is stable, no
  timestamps anywhere;
- **P3** the canonical pack ZIP unpacks back into the exact same item bytes
  (export → unpack round trip, sha256-verified);
- **P4** with `SEED_PACK_CHECK_AGAINST=<dir>` (or a directory argument): a
  checked-in or downloaded pack directory is still **current** — any drift
  (differing bytes, missing/stray item files, manifest change) exits
  non-zero.

`seed_pack.py pack --check [--against DIR]` is the same check standalone:
rebuild the pack from `data/seeds` in memory and compare byte-for-byte with
an existing pack directory.

### 7.5 Source scope

`--source global` (the default) exports `tenant_id IS NULL` rows — the global
taxonomy that ships. Pass a tenant UUID (`--source TENANT_ID` or
`SeedExportService(db, tenant_id=...)`) to treat a dedicated "template tenant"
as the source: its rows are emitted with scope stripped. This is how you curate
the canonical set in a tenant you control via the UI, then promote it to global
seeds.

### 7.6 Binary anatomy assets note

Positional marker data lives in `AnatomyStructure.display` (JSONB) and is
carried by the `anatomy_structures.json` export. Binary `anatomy_figures/`
images are out of scope for the seed ZIP (managed via the Atlas Editor).

See `backend/data/seeds/README.md` for the per-file field-schema cheatsheet.

## 8. Demo Mode (`HA_DEMO_MODE`)

Demo mode turns a Health Assistant instance into a
self-contained public demo: visitors are signed in automatically (no login
form), the data is synthetic, and a pinned banner makes the demo state
visible on every page. It is orthogonal to `HA_APP_ENV` — a hardened demo
deployment runs `HA_APP_ENV=production` + `HA_DEMO_MODE=true` (plus the
explicit `DEMO_MODE_ACCEPT_UNAUTHENTICATED=true` opt-in that production
gate requires).

`demo_mode` is an **instance fact**: `HA_DEMO_MODE`
is consumed **only when initializing an empty database** and then lives in
`instance_settings.demo_mode`. Post-init env flips are ignored with a loud
warning — the DB value stays authoritative — and unknown/missing values
fail closed to `demo_mode=false`.

### Enabling it

Set `HA_DEMO_MODE=true` (on an empty DB) in `.env`:

```bash
HA_DEMO_MODE=true
HA_DEMO_EMAIL=demo@healthassistant.local
HA_DEMO_PASSWORD=Demo1234!
```

`HA_DEMO_EMAIL` / `HA_DEMO_PASSWORD` are the single source of truth shared
with the UI screenshot capture tooling in §3. A loud warning is logged on
every boot while demo mode is on.

> **Never enable `HA_DEMO_MODE` on an instance that holds real health data.**
>
> **Fail-closed gate (audit 2026-08 CFG-H6):** with `HA_APP_ENV` set to anything
> other than `development`/`test`, `HA_DEMO_MODE=true` **refuses to boot** unless
> you also set `DEMO_MODE_ACCEPT_UNAUTHENTICATED=true` explicitly. A single
> flipped env var can no longer silently open a real instance.
> It bypasses authentication entirely — anyone who reaches the app is
> signed in as the demo user with no credentials.

### What happens on boot

When the DB fact `demo_mode` is true, the backend lifespan runs
`scripts/seed_demo.py` (idempotent — §1-4) after the normal catalog seed
pipeline, creating or reconciling the demo tenant, admin user, three
patients, and clinical data. The seeder itself carries §13 guard rails and
**refuses any other target** — loudly, exit code 2:

| Guard rail | Refuses when | Note |
|---|---|---|
| target name | the PostgreSQL database is not named `*_demo` | refused before the DB is touched; health has no SQLite/demo-dir flavor |
| instance flag | `instance_settings.demo_mode` is not `true` | unreadable facts (unmigrated schema) refuse too — fail-closed |
| `--init-demo` | the demo database is not **empty** | the flag may only initialize a fresh, migrated `*_demo` DB |

So the demo dataset only ever lands on `neuronection_health_demo`-style targets.
A `--reset` flag is deliberately absent: demo resets are volume-level —
wipe the demo deployment's DB volume and the stack re-seeds on boot.

> **Local screenshot captures** must therefore point the stack at a
> `*_demo`-named database with `HA_DEMO_MODE=true` set on first boot (a
> scratch `health_capture_demo` works). Seeding a plain dev database
> (`health_assistant`) is refused by design.

### Credential-free login

`POST /auth/demo-login` mints access + refresh tokens for the demo user
**with no credentials**. It returns 404 when the DB fact `demo_mode` is
off, so the route is inert on normal instances (a probe is audited as a
denied `auth.demo_login` event). The tokens carry the contract claim
`auth_mode: "demo"`.

**Every verifier rejects `demo` tokens while `demo_mode=false`**:
HTTP dependencies, the WebSocket handshake,
the presigned-preview Bearer path, and the refresh
path, which re-checks the live instance fact on **every rotation**. A
demo sign-in minted on a demo instance dies with the instance's
`demo_mode`; no env flip can extend it. Symmetrically, the demo principal
can never mint tenant-switch tokens (`POST /admin/tenants/{id}/switch`
refuses it with 403).

The frontend detects demo mode via `GET /auth/setup-status` (which reports
`demo_mode: true`) and auto-calls `/auth/demo-login` on the login page —
the login form is never shown.

### Demo banner

A pinned banner above the header ("Demo mode — data is synthetic…") appears
on every authenticated page, driven by the `auth_mode: "demo"` claim in
the JWT. An "Exit demo" link on the right opens the main product site.

### Session persistence across resets

If a demo deployment resets its database (volume wipe + re-seed on
boot, e.g. on a schedule), deterministic identities keep existing
sessions working across the reset: the demo tenant and user use
**deterministic UUIDs** (`DEMO_TENANT_ID` / `DEMO_USER_ID` in
`seed_demo.py`) — the same IDs are recreated every time, so outstanding
JWTs keep referencing valid entities.

If a session does go stale (transition period, or the access token expired
past 24h), the frontend auto-recovers: `getCurrentUser()` fails → the app
detects the `auth_mode: "demo"` claim → auto-logout → the login page auto-calls
`demo-login` → fresh token. The visitor never has to click anything.


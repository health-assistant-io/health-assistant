# Integrations Framework

Health Assistant includes a pluggable Integrations Framework inspired by Home Assistant. It allows users to securely connect third-party platforms (like wearables, labs, or notifications) through one extension surface. Developers: the **Integration SDK doc set** starts at [`dev/sdk/overview.md`](dev/sdk/overview.md).

The framework implements the family extension charter: extensions are
versioned artifacts with **declared** capabilities, gated at registration,
and tiered by provenance — **T0** declarative data (no code), **T1**
bundled code reviewed and shipped with the product, **T2** imported code
in a sandboxed subprocess, **T3** external processes (MCP). Imported
third-party code never runs in-process. Health ships bundled T1
connectors today and persists the **imported source lane** (T2/T3) end to
end — see [the source lane](#the-imported-extension-source-lane).

---

## Architecture Highlights

1. **Pluggable & Modular**: Each integration lives in its own isolated folder under `backend/app/extensions/builtin/`. The core platform discovers them dynamically.
   > **Scope boundary:** the Integrations Framework is for **per-provider
   > inbound sync** — connecting one specific platform (a wearable, a lab, a
   > hospital portal) that has its own OAuth, polling, or push model. It is
   > deliberately **not** a general-purpose public REST gateway; external
   > REST consumers use the [FHIR R4 facade](FHIR_R4_FACADE.md) with OAuth2
   > + SMART scopes. See [API Access Layers](API_LAYERS.md) for the
   > layer-by-audience breakdown.
2. **Two-Tiered Enablement**:
   - **System Admins** determine if an integration is globally available to the platform.
   - **Users** connect their individual accounts, provide credentials, and control sync preferences. Users can create **multiple instances** of the same integration (e.g., tracking two separate IoT scales).
3. **Dynamic Schema Setup (Config Flow)**: Instead of hardcoding forms in React, integrations expose a JSON Schema. The frontend dynamically generates the setup UI.
4. **Dynamic Documentation Trees**: The framework supports both simple (`README.md`) and complex, multi-page documentation structures. Complex integrations can define a `docs/docs-tree.json` file, which the backend parses and serves to the frontend to render interactive, nested navigation menus directly within the application.
5. **Unified Sync Engine**: A background Celery beat fires `sync_active_integrations` every 60 seconds. Each `UserIntegration` row has its own `sync_interval` (default 15 min) — the beat checks each integration's `last_synced_at + sync_interval` and skips the ones that aren't due yet. Per-integration dedup is enforced via a Redis lock (`sync_lock:{integration_id}`, `SET NX EX 600`) so overlapping beats **or a manual Sync Now** don't double-write Observations/telemetry. Sync cursors and health are **rows** in `integration_sync_state` (one per stream/cursor key), and backoff is data: a failed sync writes `next_retry_at` from the provider's `Retry-After` or an exponential streak, and the next beat reads that column (see [typed errors](dev/sdk/reference.md#the-15-extension-error-codes)). Both the background task and the manual sync endpoint delegate to a shared `IntegrationSyncService.run_sync` pipeline: pull → **ingest funnel** (`services/integrations/integration_ingest.py`: observations → biomarker-map/dedup/telemetry-split, documents → document pipeline, appointments → clinical-event surface) → catalog proposals → HITL proposals → notifications → push → flush cursors + stream health → log → commit. Each opt-in stage runs only when the extension's manifest declares the matching charter capability (`push`, `notify`, `chat-tools`) or data class (`document`, `clinical_event`, …) — declared data, gated at registration; provider methods are never probed.
6. **Secure Webhook + API Proxy Routing**: The system provides dedicated inbound routes for integrations that push or two-way-sync data:
   - `POST /api/v1/integrations/{domain}/webhook/{integration_id}` — HMAC-authenticated machine route. A `webhook_secret` is **auto-provisioned at instance creation** (shown once; stored Fernet-encrypted) and is **mandatory** — the UUID routes but never authenticates. Signature: HMAC-SHA256 over the raw body (`X-Webhook-Signature*`); add `X-Webhook-Timestamp` for the replay-proof canonical scheme. Webhook success/failure triggers the same notification dispatch as `run_sync`.
   - `ANY /api/v1/integrations/{domain}/api/{integration_id}/{path}` — generic two-way proxy, HMAC-authenticated with the instance's `api_secret` (`X-Api-Signature` HMAC-SHA256; `X-Api-Timestamp` for replay protection). The UUID identifies the instance but never authenticates it — secret-less traffic is rejected with 401 (fail-closed). See [API.md → Integrations](API.md#integrations); see [the signed routes](dev/sdk/reference.md#endpoints-the-apiv1integrations-prefix)).
   - The HMAC algorithm for both routes lives in a single SDK module — `integrations.sdk.webhook_security` (`verify_hmac_signature`, `verify_canonical_signature`, `get_signature_header`; see [webhook signature verification](dev/sdk/authoring-and-configuration.md#webhook-signature-verification-reusable-helpers)).
7. **Tool Exposure Contract**: Integrations declare the `chat-tools` capability in their manifest and implement `get_tools()` on their provider to expose LangChain tools to the chat assistant. The platform tool aggregator (`app/ai/tools/aggregator.py`; see [tool exposure](dev/sdk/authoring-and-configuration.md#tool-exposure-for-the-chat-assistant)).
8. **Notification Exposure Contract**: Integrations declare the `notify` capability and implement `get_notifications()` on their provider to emit rich, event-driven notifications (threshold alerts, HITL prompts, daily summaries, anomaly flags with action buttons + DisplayBlocks). The platform calls these hooks from `run_sync` and the webhook handler after observations are persisted. Providers can additionally declare their notification **kinds** statically via `get_notification_types()` so users can toggle individual kinds on/off without losing the rest — surfaced in a per-integration "Notifications" tab + the central `/notifications/settings` hub. Each notification carries a backend-stamped `payload.preferences` hint so users can mute a kind inline from the notification itself. Three filter layers compose (per-source ∩ per-channel ∩ per-integration-instance-type); see [notifications](dev/sdk/authoring-and-configuration.md#notifications-event-driven-rich-actionable)).
9. **Secret Encryption**: Integrations declare secret config fields via `get_secret_fields()`; the SDK encrypts them at rest (Fernet); see [secrets](dev/sdk/authoring-and-configuration.md#secret-encryption-at-rest)).
10. **Integration-Driven Write Hooks (Clinical Events & Examinations)**: Integrations can opt-in to pull longitudinal records — FHIR `Condition` / `EpisodeOfCare` (clinical events) and FHIR `Encounter` (examinations) — alongside observations, by declaring the `clinical_event` / `examination` data classes in their manifest's `itemSchema` and implementing the matching `pull_*` hooks. The platform's `run_sync` pipeline checks each declaration, resolves a service-context actor from the integration's owning user (`integration_actor.resolve_integration_actor`), and writes each record through the canonical service (`clinical_event_service.create_event` / `examination_service.create_examination`) — same RBAC, same audit provenance, same tenant scoping as interactive UI writes. Dedup is automatic when the provider sets `external_id` on the payload (partial unique index on `(tenant_id, patient_id, source_integration_id, external_id)` enforces it at the DB layer); see [write hooks](dev/sdk/authoring-and-configuration.md#clinical-events--examinations-write-hooks)).
11. **Catalog Proposal Contributions**: Integrations declare the `catalog_proposal` data class and implement `pull_catalog_proposals` to contribute new catalog entries — biomarker definitions, medication catalog rows, taxonomy concepts, and typed concept edges. The platform's `run_sync` pipeline auto-applies each proposal through `catalog_proposal_service.apply_proposal`, which routes by kind to the canonical write service and stamps `ConceptProvenance.INTEGRATION` on `ConceptEdge.source` (plus a `meta_data["_provenance"]` tag on biomarkers — the model has no dedicated column). Idempotent per kind (slug for biomarkers/concepts, name for medications, endpoints for edges), so re-syncs are no-ops. Closes the gap left by `ConceptProvenance.INTEGRATION` (declared in the enum but unwritten before this hook); see [catalog proposals](dev/sdk/authoring-and-configuration.md#catalog-proposals-auto-applied)).
12. **Integration HITL Proposals (Human-in-the-Loop Review)**: Integrations can opt-in to queue writes for human review instead of auto-applying them — by declaring the `hitl_proposal` data class and implementing `pull_hitl_proposals` (+ optional `handle_proposal_resolution` callback). Each proposal persists as a row in the one `proposals` table (`source='extension'`, `source_ref` = the integration instance, `kind_id` → the seeded `proposal_kinds` contract vocabulary) with status `proposed`. The owner (or a tenant admin) resolves via three endpoints — `GET /api/v1/integrations/instance/{id}/proposals`, `GET .../proposals/{id}`, `POST .../proposals/{id}/resolve` — and on approve the resolver validates the payload against the kind's contract schema and delegates to the kind's resolver: the same `apply_proposal` path as #11 for catalog kinds, or the canonical clinical service for the four patient-record kinds (`create_event`, `create_examination`, `add_medication`, `add_biomarker_to_examination`). Dedup via a tenant-scoped `dedup_key` partial unique index keeps re-syncs from spamming the inbox. The closed state machine is `proposed → approved → applied` (or `failed` / `rejected` / `expired` / `superseded`), and resolve is idempotent — a repeated decision returns the stored result, a conflicting one is 409. See [HITL proposals](dev/sdk/authoring-and-configuration.md#hitl-proposals-human-review).
13. **Document Pull (Opt-in Document Ingestion)**: Integrations that can deliver document bytes upstream — a hospital portal integration pulling scanned lab reports, a fax-to-email gateway, a wearable companion app syncing ECG printouts — declare the `document` data class and implement `pull_documents` to hand them to the platform's OCR + LLM extraction pipeline. The platform's `run_sync` pipeline routes each `DocumentPull` spec through `document_service.ingest_document_bytes` — the same canonical ingestion path the UI upload endpoint uses — and fires the OCR Celery task when requested. Two per-sync caps (`INTEGRATION_MAX_DOCS_PER_SYNC = 20`, `INTEGRATION_MAX_DOC_BYTES_PER_SYNC = 50 MiB`) protect against runaway providers. Documents can be linked to a pulled examination via `examination_external_id` (resolved against the exams pulled in the same sync) and categorized via `category_concept_slug` (resolved via the catalog). When the provider sets `external_id` on the spec, the platform dedups at the DB layer via a partial unique index on `(tenant_id, patient_id, source_integration_id, external_id)` — the same pattern as #10's events/exams; dedup hits return the existing row as-is (no re-file-write, no re-OCR); see [document pull](dev/sdk/authoring-and-configuration.md#document-pull)).

---

## How It Works Under the Hood

### 1. Startup & Discovery
FastAPI initializes the `IntegrationRegistry` during startup. It scans the `backend/app/extensions/builtin/` folders, reads each `manifest.json`, and queries the database (`system_integrations` table) to see which discovered domains are marked `is_enabled=True`. Only enabled integrations have their Python classes loaded into memory.

### 2. User Configuration (Config Flow)
When a user clicks "Add Integration", the frontend requests `/api/v1/integrations/{domain}/config-flow`. The backend returns the JSON schema defined by the integration, and the frontend renders a dynamic form. 

### 3. Persistence & Security
The user's form inputs are saved in the `user_integrations.user_config` JSON column — **configuration only**. Secrets never live there: sealed credential material is one row per `(instance, kind)` in `integration_credentials` (at-rest cipher wrapper + `kid` fingerprint, scopes, expiry, `rotated_at`/`revoked_at`), and sync cursors/health are rows in `integration_sync_state`. A unique UUID is assigned to this connection. Flows that collect a credential slot override `BaseConfigFlow.store_credentials` — the submit endpoint calls it after the instance row exists and before commit, so a flow can seal the slot (and strip its transient carrier from `user_config`); a sealing failure aborts the whole submit rather than falling back to plaintext.

Because the system allows multiple instances of the same integration (e.g., configuring two different Webhook endpoints for two different phones), all instance management, deletions, syncs, and custom actions are routed via this `integration_id` (UUID) rather than the generic `domain`.

### 4. Background Syncing (Polling)
`celery_app.py` has a periodic beat (every 60 s) that triggers `sync_active_integrations` in `tasks.py`. This task loops through all active `UserIntegration` records whose `integration_sync_state.next_retry_at` gate has passed, loads their specific provider instance, and delegates to `IntegrationSyncService.run_sync` — the shared pipeline that runs (in order): credential gate → pull observations → **ingest funnel** (dedup, biomarker-map, telemetry-split, documents, appointments) → labs (opt-in `lab_report` providers — `pull_labs` through `ingest_labs`, deduped on tenant/patient/code/effective) → catalog proposals (opt-in) → HITL proposals (opt-in) → notifications → push → flush cursors + per-stream health → log → commit. Each integration's per-row `sync_interval` (default 15 min) gates whether the beat actually syncs. The manual Sync Now endpoint (`POST /api/v1/integrations/{id}/sync`) also delegates to `run_sync`.

**Per-integration dedup lock (audit C4):** `run_sync` acquires a Redis lock keyed `sync_lock:{integration_id}` via `SET NX EX 600` (sync hard timeout). If the lock can't be acquired (another worker or a manual sync is already running this integration), the sync is SKIPPED — no duplicate writes. The lock is released in a `finally` block; if the process crashes mid-sync, the 600 s TTL expires it. Redis-down degrades gracefully to the legacy always-sync mode but logs a warning.

**Backoff is data (`next_retry_at`):** when `run_sync` catches a typed `rate-limited` error carrying a `Retry-After` hint, it writes `next_retry_at` on every `integration_sync_state` row of that instance (the later of the hint and the exponential streak, capped at 1 h). A success resets `consecutive_failures` and clears the gate. The beat runs one query per cycle (`backoff_gate`) and skips instances whose retry gate is still in the future — no Redis cooldown key, no code-embedded state, and the manual Sync Now path reuses the same rows.

### 5. Webhooks (Push)
When a third party pushes data to the unique `/api/v1/integrations/{domain}/webhook/{integration_id}` URL, the framework intercepts it, loads the specific `UserIntegration` matching that UUID, **verifies the mandatory HMAC signature** (the UUID is only a routing key, never a credential), and hands the payload off to the provider's `handle_webhook()` (see [webhook handlers](dev/sdk/authoring-and-configuration.md#webhook-handlers-opt-in-push)). Every bundled parser schema-validates its payload first (`app/extensions/builtin/webhook/validation.py`: bounded size/depth/nodes, section lists of objects, declared field types) and rejects an invalid delivery with a typed `protocol`/`invalid` error — the route persists a typed failure row (`error_class`) and answers `4xx` with `{"code", "message"}`. Nothing is swallowed: a malformed body, a hostile payload and a parser crash all become visible rows. Failures fire the same auth/data-failure escalation path (owner + tenant admins).

### 6. FHIR Normalization & Telemetry Split
Whether data is pulled or pushed, the framework expects the integration to return standardized FHIR `ObservationCreate` objects. The core engine handles saving these observations to the database and mapping them to the correct semantic Clinical Ontology (Biomarkers).

**Frequency-based routing (FHIR vs TimescaleDB):** observations linked to a `BiomarkerDefinition` flagged `is_telemetry=True` are routed to the `telemetry_data` TimescaleDB hypertable (heart rate, steps, CGM, etc.); everything else lands in the standard `fhir_observations` table. This routing is the telemetry-split step inside `IntegrationSyncService.run_sync` (`apply_telemetry_split`), which is called by the background task and the manual sync endpoint (both via `run_sync`). The webhook handler inlines the same logic — the only persistence path not currently routed through `run_sync`, but the post-sync notification dispatch is now shared via `post_sync_notifications` (see `integration_sync_service.py`).

The helper stamps the `performer` reference (`Integration/<id>`) on FHIR rows that don't already have one, and emits one **long-format** `TelemetryDataModel` row per telemetry observation (`slug`/`value`/`unit`/`patient_id` columns) — uniform across every biomarker, no dedicated-column or JSONB-catch-all branching.

### 7. Interactive Documentation Rendering
When a user views an integration's details in the UI, the frontend requests `/api/v1/integrations/{domain}/documentation`. The backend checks the integration's root folder for a `docs/docs-tree.json`. If found, it parses the JSON tree and returns it alongside the requested markdown file. The frontend uses this metadata to dynamically render a sidebar navigation menu, allowing users to browse complex SDK references or setup guides without leaving the platform.


## Adding a data source (connector framework)

Adding a connector is an **extension plus rows, never a migration**:

1. One directory under `backend/app/extensions/builtin/<name>/` with a manifest
   (declared capabilities + `itemSchema` data classes — registration gates on
   declarations, never on method probing).
2. Sync cursors and health are rows: the provider uses
   `self.get_cursor(integration, stream, key=..., default=...)` /
   `self.set_cursor(integration, stream, value, key=..., kind=...)` /
   `self.clear_cursor(...)`. `stream` is one of the closed data classes
   (`observations`, `documents`, `appointments`, `labs`, `telemetry`);
   `key` names a provider-scoped sub-cursor (a FHIR server pulls several
   resource types under `observations`); `kind` is `timestamp`, `page_token`,
   `etag` or `commit`. The engine hydrates the instance's buffer before the
   run and flushes it in the same transaction — a provider never touches
   `user_config` for state.
3. Credentials: `services/integrations/integration_credentials.py` seals a payload under the
   instance id (`seal_credential`), reads it fail-closed (`read_credential`),
   rotates in place under the current key's `_kid` (`rotated_at`) and revokes
   (`revoke_credential`). A revoked/unreadable/expired credential makes
   `run_sync` raise the typed `auth` error before any pull — the sync fails
   closed and the instance flips to `ERROR`. The config-flow seal path is
   `store_credentials` (called after the instance row exists, before commit).
4. OAuth connectors (the Withings pattern): the config flow sets
   `is_oauth = True` and the provider overrides `begin_oauth` /
   `complete_oauth` — the platform routes `/{domain}/oauth/start` and
   `/{domain}/oauth/callback` through them with a one-shot Redis `state`
   (`OAuthStateStore`). The token exchange/refresh ride the guarded
   transport and the WHOLE credential set (client secret + access/refresh
   pair) lives in ONE sealed `oauth2` slot whose `access_expires_at` /
   `refresh_expires_at` columns drive the refresh-before-pull gate: a due
   refresh reseals the slot in place, a failed refresh raises the typed
   `auth` error before any data call.
5. Every write goes through `services/integrations/integration_ingest.py`:
   `ingest_observations` (biomarker mapping, dedup, telemetry split,
   mapping-gap recording), `ingest_telemetry` (slug registry + device
   registration + canonical units), `ingest_documents` (the canonical document
   pipeline, OCR via the AI gateway) and `ingest_appointments` (examinations +
   clinical events). Dedup keys are the 2026-07 convention: documents/exams/
   events use `source_integration_id` + `external_id`; observations and
   telemetry rows dedup on their natural key (patient, timestamp, metric
   identity/value or device).
6. Failures are typed: raise the charter codes (`auth`, `rate-limited`,
   `net-denied`, `protocol`, …). `run_sync` writes an
   `IntegrationSyncLog` row with `error_class` and updates
   `last_error_class`/`consecutive_failures`/`next_retry_at` on the stream
   rows. Providers never return an empty list to hide an error.

---

## Enabling Integrations Globally

Integrations are **enabled by default** (the admin-curated host profile of
the family extension charter): a bundled extension dropped into
`backend/app/extensions/builtin/` is available the moment the registry
discovers it, and only an explicit `is_enabled=false` row written by a
SYSTEM_ADMIN hides it. Disabling (or re-enabling) happens via the Admin
UI at `/admin/system/integrations`, or directly via SQL:

```sql
INSERT INTO system_integrations (domain, is_enabled) 
VALUES ('your_integration_domain', true)
ON CONFLICT (domain) DO UPDATE SET is_enabled = true;
```

---

## The imported-extension source lane

Bundled T1 connectors are not the only lane: health persists the charter's
**import pipeline** (T2 sandboxed code, T3 external MCP processes) as four
tables, all digest-pinned and fail-closed. The charter is the law; these
rows are health's persistence for it.

### Sources — where content comes from

`extension_sources` is one registered origin: `kind` (`git | file |
registry`), the `uri`, the pinned `ref_spec`, a `trust_tier` of `T2` or
`T3` (the lane's code-only scope), `enabled`, and the fetch bookkeeping
(`last_check_at`/`last_error` — a failed fetch never moves the pins).
Private-repo credentials ride a **secret-slot reference**
(`auth_secret_ref`), never an inline token — the descriptor schema has no
field that could hold one. Descriptors validate against the family
`extension-sources.schema.json`: production stays https-only by structure;
the local `file` kind is the dev/test variant — it is gated off outside
development boots and confined to allowlisted roots
(`HA_EXTENSION_LOCAL_SOURCE_ROOTS`, empty = disabled).

### Versions — immutable digests, approval required

`extension_source_versions` holds **immutable** resolved content: the
content `digest` (sha256 over the regular files — symlinks never
contribute and never execute), the `manifest_digest`, the validated
`envelope` and `contract_version`. Registration runs the ONE family
registration decision (`contract.validate_registration`) with the source's
trust tier as the caller claim — a claim contradicting the provenance
derivation (`entry` declares code: imported ⇒ T2, `+ mcp` ⇒ T3) is the
typed `provenance derives tier …` problem. The outcome is a ROW:

- `pending` — resolved, awaiting the explicit admin approval
  (`POST /api/v1/extensions/sources/versions/{id}/approve`);
- `rejected` — the typed problems stay in the row's `reason`; a
  self-disable is never a log-and-skip (bundled registrations that
  self-disable surface at
  `GET /api/v1/admin/integrations/registration-failures`);
- `revoked` — pulls an approved digest back.

A changed digest is a NEW row requiring re-approval — there is no
auto-apply path at all for code, and the running instance never
auto-upgrades: `active_version` is the latest **approved** row and a
tampered re-import cannot touch it.

### Consent — bound to the digest

`extension_consents` records one `(user, version, capability)` decision
(`granted | denied`). Consent binds to a manifest **digest** via the
version row, so an upgraded extension re-prompts for what is new while
unchanged capabilities carry their grants forward as new rows (the
browser-permission model). The surface contract:

- `GET /api/v1/integrations/consents/pending` — approved versions the user
  has not decided on;
- `GET /api/v1/integrations/consents/versions/{id}/delta` — the 8C
  change-summary: added capabilities force the re-prompt, unchanged ones
  keep their grants, removed ones are reported;
- `POST /api/v1/integrations/consents/versions/{id}` — records decisions
  behind the host-derived consent echo `{digest, capabilities, version}`;
  a forged echo or an undeclared capability is the typed `invalid`.

The charter's disclosures (T2 sandbox / T3 perimeter) render **verbatim**,
keyed by `disclosureKey` — paraphrasing them away is a conformance
failure. Secret fields (`x-secret`) seal per version: the sealed payload
stamps the digest it was sealed for, so an upgrade reports the slots stale
and re-binds instead of silently reusing secret material. The frontend
`ConsentDeltaView` renders exactly this delta.

### Isolation posture — runtime evidence

`extension_isolation_posture` records, per version and host profile, the
`requested_tier` next to the `derived_tier` (`contract.derive_tier` is the
one derivation — a contradiction is queryable evidence) and the graded
containment answer from DETECTION (`isolation.posture()`: `kernel` only
when bubblewrap is real, else `none` — never a constant). The spawn seam
(`app/extensions/runtime/sandbox.py`) composes the three laws: tier is
provenance, the host profile's `t2IsolationByPlatform` is policy data, and
detection is truth. A host that cannot provide the kernel grade answers
the typed `unsupported` with the reason and the remedy — never a silent
spawn below the kernel boundary.

### Status of the lane

Local (`file`) sources import end-to-end on a dev box: register → sync →
approve → consent → posture. Git and registry descriptors validate and
persist, and their sync answers the typed `not-ready` until their
transport lands (the import pipeline's git lane is career's reference
implementation). Health's charter row currently declares
`t2IsolationByPlatform: {linux: none}` and does not list T2 — so T2
imports land (and are refused) with the typed profile reason until the
charter's health profile admits T2 with the kernel grade; the lane's
persistence, seams and typed refusals are in place either way.
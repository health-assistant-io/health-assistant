# Core Concepts

Every noun the rest of this set uses is defined here, once. The ASCII
diagram first, then the nouns in the order they connect.

```
 extension registry (read-only after boot)
 ┌───────────────────────────────────────────────────────────────────────┐
 │  builtin/{domain}/                                                    │
 │    manifest.json ←→ contracts/extension-manifest.schema.json          │
 │      │  (validated AT REGISTRATION; invalid ⇒ never loads)            │
 │      ├── provider.py      (BaseHealthProvider)                        │
 │      └── config_flow.py   (BaseConfigFlow)                            │
 └──────────┬─────────────────────────────┬──────────────────────────────┘
            │ key: envelope id / alias    │
 ┌──────────▼─────────────────────────────▼──────────────────────────────┐
 │ engine & surfaces:  sync service · workers/beat · REST endpoints      │
 │   sync pipeline per instance:                                         │
 │     pull → FHIR-validate+map → telemetry/FHIR split → write hooks     │
 │     (events, exams) → catalog+HITL proposals → documents → notify →   │
 │     push → log → commit                                               │
 └──────────┬────────────────────────────────────────────────────────────┘
            │ user_integrations row = the instance
 ┌──────────▼────────────────────────────────────────────────────────────┐
 │ instance: status, sync_interval, user_config (JSONB, x-secret sealed) │
 │           machine secrets (api_secret / webhook_secret)               │
 └───────────────────────────────────────────────────────────────────────┘
```

## The nouns

### Extension (+ the per-instance model)

An **extension** is the deployed module: one directory under
`backend/app/extensions/builtin/{domain}/` with a `manifest.json`
(the charter envelope), a `provider.py` and (almost always) a
`config_flow.py`. The directory name is the underscore-form **domain**
(`dev_dummy`); the manifest's envelope `id` is the hyphenated charter form
(`dev-dummy`). The registry keys extensions by their envelope id, and
every lookup accepts either form
(`app/extensions/registry.py::domain_of`). A deployed copy bound to a
patient is an **instance** — its own
[Instance](#instance) noun below.

### Kind

The charter's closed set of extension shapes; health hosts `connector`
(outbound data flows: pull/sync + push) and `bridge` (inbound surfaces:
`inbound-webhook` / `inbound-api`). Three of the five bundled extensions
are bridges (`dev_dummy`, `webhook`, `health_assistant_bridge`), which is
why inbound machinery is a first-class surface here.

### Capability

The manifest's `capabilities` list is the closed charter taxonomy — the
engine's **only** answer to "can this extension do X?"
(`integration_registry.has_capability`). Health extensions declare from:
`sync`, `push`, `notify`, `chat-tools`, `secrets`, `state`,
`inbound-webhook`, `inbound-api`, `net`, `llm` (catalog-side entries the
registry honours on this host — the closed taxonomy lives in the charter).
An undeclared capability is never called; there are no `supports_*` probe
hooks left.

### Data class (the itemSchema profile)

For connector-kind extensions, the `itemSchema` property **names** are the
item contracts the engine may pull: `observation`, `clinical_event`,
`examination`, `document`, `medication`, `allergy`, `immunization`,
`catalog_proposal`, `hitl_proposal`. Declaring the data class + overriding
the matching `pull_*` hook is the complete opt-in. The classes map to the
FHIR-shaped payloads the engine persists
(`ObservationCreate`, `ClinicalEventCreate`, `ExaminationCreate`, …).

### Sync (the pipeline)

The engine's one ingestion path. A Celery beat fires every 60 seconds and
checks each ACTIVE instance's `sync_interval` (default 15 min); a manual
"Sync Now" rides the same `run_sync` service. A Redis lock
(`sync_lock:{integration_id}`, `SET NX EX 600`) keeps overlapping beats or
a manual sync from double-writing. Stages, each run only when the
extension's declaration requires it:

**pull → FHIR-validation + biomarker-map → telemetry/FHIR split →
clinical events → examinations → catalog proposals → HITL proposals →
documents → notifications → push → sync log → commit.**

Per-stage failures are logged, not aborts; the sync log row carries the
counts and (on failure) the typed error code.

### Secret slot (x-secret + machine secrets)

Two secret families, one law — never plaintext at rest:

- *Declared credential fields*: `configSchema` properties marked
  `"x-secret": true`. Values live only the Fernet-sealed store, masked as
  `"***"` on read; a plaintext secret in stored config is a
  registration-time failure, and the config flow's `get_secret_fields()`
  declaration must agree with the manifest (the registry's honesty gate).
- *Provisioned machine secrets*: every webhook / two-way-API instance is
  auto-provisioned with `webhook_secret` / `api_secret` at creation, shown
  **once** in the config-flow response, sealed with the instance id as
  context binding, and mandatory on those routes (unsigned traffic →
  rejected; the one exception is the unsigned `GET /status` pairing probe,
  `{status, server_time}` only).

### Provider / config flow

The two implementation classes the registry looks up by base class:

- `BaseConfigFlow` — the dynamic UI: `get_schema()` (JSON schema the
  frontend renders), `validate_input()`, the secret-field + per-user-cap
  declarations, and the storage/read/use transforms for x-secret fields.
- `BaseHealthProvider` — the logic: pull hooks, webhook/API handlers,
  custom actions, chat tools, notifications, error mapping. The registry
  calls `provider.setup()` once at app startup with the
  `SystemIntegration.global_config` JSONB.

## The typed-error connective tissue

Every capability call crosses an extension boundary as a typed
`ExtensionError` (one closed 15-code enum, closed `retryable` sub-vocabulary;
[Reference](reference.md#the-15-extension-error-codes)). The routing rules
— which codes flip the instance to ERROR, skip a sync, or log only — are
law in [Security & Gotchas](security-and-gotchas.md) and tabulated in the
Reference. Raw exception strings never cross an extension boundary.

## Where to read the code

| Noun | Home |
|---|---|
| registry (discovery, capability/data-class gates) | `backend/app/extensions/registry.py` |
| provider/config-flow bases | `backend/app/extensions/sdk/base.py` |
| sync pipeline | `backend/app/services/integration_sync_service.py` |
| polite transport | `backend/app/extensions/{runtime/http.py,polite_http.py}` |
| the reference extension | `backend/app/extensions/builtin/dev_dummy/` |

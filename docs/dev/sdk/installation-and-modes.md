# Installation & Modes (internals)

The user-facing half of this chapter — connecting an integration in the
app and what every screen does — lives at
[Connecting an integration](../../user/connect-integrations.md). This page
is the author-side internals: where extension code lives on disk, what the
registry does with it, the admin gating, and how health's host profile is
shaped.

## Bundled vs imported — health is bundled-only

Per the charter's host profile, health ships a **bundled-only,
admin-curated** steady state: the extensions in the image are discovered at
boot and **enabled by default**; a SYSTEM_ADMIN writing an explicit
`is_enabled=False` row is the only way one goes off. Nobody imports
third-party code and there is no consent/import machinery health-side —
imported extensions (git sources, digests, consent records) are career and
desktop features, deferred for health ("no GitOps import on this host").
Consequently:

- Tier `T1 bundled` is the whole game on this host: "imported" code does
  not exist outside the repo review process.
- The authoring bar is therefore *reviewed with the product* — anything
  landing under `builtin/` is code the family ships, exactly like core.
- External parties never load code into your server: they connect to the
  *already-bundled* surfaces — the bridge API (their client SDKs are
  consumer satellites under `integrations/health-assistant-bridge/`, never
  imported by the backend) and webhooks.

## The registry (discovery, validation, honesty gates)

`app/extensions/registry.py`:

1. **Reads manifests** — one directory per extension under `builtin/`
   (underscore-domain form), each must carry `manifest.json`.
2. **Validates at registration** against the vendored family schema
   (`contracts/extension-manifest.schema.json`, `$defs.bundledManifest`).
   An invalid manifest **never loads** — the extension self-disables with
   a logged reason rather than half-running, and reserved id prefixes are
   rejected.
3. **Loads code** — imports `provider` + `config_flow`, finds the
   `BaseHealthProvider` / `BaseConfigFlow` subclasses by base class, calls
   `provider.setup(system_global_config)` once.
4. **Runs the secret honesty gate (D19)** — every field the config flow
   declares via `get_secret_fields()` must be `x-secret`-marked in the
   manifest `configSchema`, or registration *raises*: the two declarations
   may never drift apart.
5. **Never stores per-domain code in endpoints.** Every platform surface
   (capability gates, sync, tools aggregation, notification kinds,
   machine-secret provisioning) reads the registry; adding an extension
   touches no endpoint.

## Admin gating

- **Enabled by default** (`admin-curated`): `SystemIntegration.is_enabled`
  defaults to available; an explicit false row skips the domain at boot
  (`admin → integrations` in the UI; `POST /api/v1/integrations/admin/{domain}/enable|disable`
  server-side vs `GET /available`+`/active`).
- The config-flow endpoints refuse **disabled** domains with
  `400 Integration is not enabled by system admin.`
- Gating is `GET/POST /api/v1/admin/integrations[/{domain}/enable|disable]`
  server-side (SYSTEM_ADMIN); `GET /available` + `/active` are the user view.
- The documentation endpoint deliberately does *not* check enablement
  (users read docs before enabling); the schema endpoint (`/schema`)
  serves only schema-capable extensions (today: `health_assistant_bridge`).

## Modes: server vs desktop client

The backend runs in two consumption modes that matter to extensions:

| Mode | What runs | Extension impact |
|---|---|---|
| Server (web/PWA + bridge clients) | FastAPI + worker/beat + Flower, frontend served separately | every hook available; beat drives sync |
| Development scratch | uvicorn against a scratch DB, keys auto-generated (see the [Quickstart](quickstart.md)) | identical surface; signing keys are per-process (restarts orphan sessions) |
| Desktop (Android companions) | the Android app talks *to* this server over the bridge API | no extension code runs on the device; the phone is a bridge client |

## Where artifacts live on disk

| Artifact | Path |
|---|---|
| Extension source (`manifest.json`, `provider.py`, `config_flow.py`) | `backend/app/extensions/builtin/{domain}/` |
| Per-extension docs surface (in-app "Documentation" dialog) | `backend/app/extensions/builtin/{domain}/docs/` + `docs-tree.json` (the category-list form) |
| Published wire schemas (schema-capable bridges) | `backend/app/extensions/builtin/{domain}/schema/` |
| Family contract vendored modules | `backend/app/extensions/{contract,polite_http,conformance}.py` |
| Authoring SDK | `backend/app/extensions/sdk/` (base, auth, fhir, notifications, documents, catalog, proposals, display, observation_builder, secrets, webhook_security) |
| Bridge client SDK satellites (python/ts/kotlin) | `integrations/health-assistant-bridge/` — never imported by the backend |

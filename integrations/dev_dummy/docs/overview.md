# Dev Dummy Integration

The `dev_dummy` integration is a **reference implementation** — it exercises every capability the Integrations SDK exposes so that authors writing real integrations have one place to copy from.

## Why it exists

Every hook in `integrations.sdk.base.BaseHealthProvider` and `BaseConfigFlow` has at least one demonstration here, gated by a config-flow toggle so you can enable capabilities in isolation and watch what the engine does on the next sync.

## What it does (v2.0.0+)

The integration synthesises data instead of calling a real API, so it works fully offline. On every sync it can:

- **Pull** simulated biomarker observations (heart rate, blood pressure, body weight).
- **Pull categorical observations** (mood: `good` / `ok` / `bad`) — demonstrates `ObservationBuilder.set_value_string` (FHIR `valueString`).
- **Emit notifications** for threshold breaches (HR > 100, BP ≥ 130/85), a per-sync summary, and a critical "sensor malfunction" alert when the HR-glitch toggle fires.
- **Receive HMAC-signed webhooks** — `handle_webhook` validates an `X-DevDummy-Signature` header against the Fernet-encrypted `webhook_secret` config field.
- **Serve a small REST API** for headless clients — `GET status`, `GET cursor`, `POST reset`, `POST echo`.
- **Expose two LangChain tools** to the chat assistant (opt-in via `enable_tools`).
- **Pull clinical events** (`supports_clinical_events`) — synthesises a flu-episode `ClinicalEventCreate` with dedup-friendly `external_id`.
- **Pull examinations** (`supports_examinations`) — synthesises an annual-checkup `ExaminationCreate`.
- **Auto-apply catalog proposals** (`supports_catalog_proposals`) — proposes a "Sleep Quality Score" biomarker definition.
- **Queue HITL proposals** (`supports_hitl_proposals`) — queues a "Stress Index" concept for human review; advancing the cursor on resolve.
- **Pull documents** (`supports_documents`) — synthesises a small text "lab report" once per instance.
- **Demonstrate the secret-field lifecycle** — `webhook_secret` is Fernet-encrypted at rest, masked as `***` on read, decrypted only when the provider needs it.
- **Demonstrate the per-user instance cap** — `max_instances_per_user = 3` (enforced by the endpoint on create).

Each opt-in toggle is documented in the config-flow schema, so the in-app setup screen doubles as a quick reference.

## Where to read the code

| File | Role |
|---|---|
| `integrations/dev_dummy/provider.py` | All provider hooks, organised by capability with `§A`–`§P` markers. |
| `integrations/dev_dummy/config_flow.py` | JSON schema, validation, secret fields, instance cap. |
| `backend/tests/test_dev_dummy_provider.py` | Unit tests pinning every capability. |
| `integrations/dev_dummy/docs/capabilities.md` | Capability-to-toggle-to-file map. |

## Compatibility

- Requires Health Assistant core with the Integrations SDK (`integrations.sdk`) loaded.
- Works without `INTEGRATION_SECRET_KEY` configured *unless* you set a `webhook_secret`. With a secret set, the platform endpoint refuses to save the config until the Fernet key is configured (fail-fast — the cipher raises `RuntimeError`).
- The `enable_tools` toggle gracefully no-ops when `langchain-core` isn't installed.

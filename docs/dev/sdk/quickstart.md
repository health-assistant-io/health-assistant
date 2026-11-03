# Quickstart — a scratch server, dev-dummy, and your first synced observations

One path, about ten minutes: spin a scratch database, stand up the Health
Assistant backend against it, connect the `dev_dummy` reference extension,
trigger a sync — and real observations land. Every command below was
executed against a throwaway Postgres + Redis (docker), exactly as shown;
JSON responses are the actual output, trimmed to the fields that matter.
The same pipeline is pinned by `backend/tests/test_dev_dummy_provider.py`
(the provider hooks, no DB needed) and by the sync engine's suites
(`backend/tests/test_integration_sync_telemetry_split.py`).

**Prerequisites**

- A `backend/` checkout with its environment (`uv sync` — the repo's dev
  flow runs everything through `uv run`).
- Docker, for a scratch TimescaleDB + Redis (the image is the same one the
  repo's dev stack uses, `timescale/timescaledb:latest-pg16`).
- A scratch directory: `mkdir -p /tmp/ha-sdk-quickstart && cd /tmp/ha-sdk-quickstart`
  (nothing below writes into your working tree).

**Success criterion:** one sync returns
`"metrics_synced": 5`, and `GET /observations` lists blood pressure, body
weight, and mood rows for your patient while heart rate lands in the
telemetry store (TimescaleDB) — that split is the engine's by-design
routing, not a bug in your sync.

## 1. Scratch Postgres + Redis

```bash
docker run -d --name ha-qs-pg \
  -e POSTGRES_USER=qs_owner -e POSTGRES_PASSWORD=qs_pw_ha_sdk \
  -e POSTGRES_DB=ha_sdk_qs -p 127.0.0.1:5437:5432 \
  timescale/timescaledb:latest-pg16
docker run -d --name ha-qs-redis -p 127.0.0.1:6381:6379 redis:7-alpine
```

Health's schema needs TimescaleDB (hypertables), so the plain `postgres`
image will not do.

## 2. Migrate the scratch database

Run from `backend/`; the `POSTGRES_*` / `REDIS_URL` environment
variables are the single DB/Redis knobs the settings read:

```bash
cd backend
POSTGRES_USER=qs_owner POSTGRES_PASSWORD=qs_pw_ha_sdk \
POSTGRES_DB=ha_sdk_qs POSTGRES_PORT=5437 \
REDIS_URL=redis://localhost:6381 \
uv run alembic upgrade head
```

Real output (the tail):

```text
INFO  [alembic.runtime.migration] Running upgrade c1h2a3t4b5r6 -> d1e2c3i4s5i6, Decision models: transport seam + PHI guard.
INFO  [alembic.runtime.migration] Running upgrade d1e2c3i4s5i6 -> t1e2m3p4r5o6, Temperature becomes optional.
INFO  [alembic.runtime.migration] Running upgrade t1e2m3p4r5o6 -> c1h2a4t5t6r, Model rows gain the chat_transport seam.
```

## 3. Stand up the dev server on the scratch DB

Same environment, then boot the FastAPI app (uvicorn is the direct
equivalent of what `scripts/run-dev.sh` runs under honcho):

```bash
POSTGRES_USER=qs_owner POSTGRES_PASSWORD=qs_pw_ha_sdk \
POSTGRES_DB=ha_sdk_qs POSTGRES_PORT=5437 \
REDIS_URL=redis://localhost:6381 \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`HA_APP_ENV` defaults to `development`, so the first-run setup wizard is
open and per-purpose signing keys are generated per-process
(the "keys are auto-generated in dev" rule — restarts orphan sessions;
that's fine for a scratch run).

## 4. Initialize, create a patient, find the extension

```bash
# First-run setup wizard: creates tenant + SYSTEM_ADMIN, returns tokens.
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/setup \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@quickstart.local","password":"quickstart-pass-1","full_name":"QS Admin","tenant_name":"Quickstart"}'
# → {"access_token": "...", "refresh_token": "...", "token_type": "bearer", ...}

TOKEN=<paste the access_token>

curl -s -X POST http://127.0.0.1:8000/api/v1/patients \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":{"family":"Demo","given":["Ada"]},"gender":"female","birth_date":"1990-01-01"}'
# → {"id": "4b0939ec-…", "gender": "FEMALE", "birth_date": "1990-01-01", …}

PATIENT_ID=<paste the patient id>

# The bundled extensions are enabled by default (admin-curated host profile):
curl -s "http://127.0.0.1:8000/api/v1/integrations/available" \
  -H "Authorization: Bearer $TOKEN"
```

Real output of the catalog (trimmed per row):

```text
dev_dummy | Developer Dummy Data
fhir_server | FHIR Server (Hospital Sync)
health_assistant_bridge | Health Assistant Bridge
mcp_client | MCP Client
webhook | Universal Webhook
```

## 5. Connect dev-dummy through the config flow

The configuration UI is data (a JSON schema the frontend renders —
[Core Concepts → config flow](core-concepts.md)); the same endpoint works
over curl:

```bash
curl -s "http://127.0.0.1:8000/api/v1/integrations/dev_dummy/config-flow" \
  -H "Authorization: Bearer $TOKEN"
```

Real response (trimmed):

```text
instance_name       | Instance Name                | default "Dev Dummy"
sync_interval       | Sync Interval (Minutes)      | default 15
webhook_secret      | Webhook Signing Secret       | optional
generate_heart_rate | Generate Heart Rate (bpm)    | default true
generate_blood_pressure | Generate Blood Pressure (mmHg) | default true
...
```

Submit it (this creates the **instance** and returns the platform-provisioned
machine secrets — shown exactly once):

```bash
curl -s -X POST "http://127.0.0.1:8000/api/v1/integrations/dev_dummy/config-flow?patient_id=$PATIENT_ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"instance_name":"SDK Quickstart","sync_interval":5,"generate_heart_rate":true,
       "generate_blood_pressure":true,"generate_weight":true,"generate_mood":true,
       "enable_clinical_events":true,"enable_examinations":true,
       "enable_catalog_proposals":true,"enable_hitl_proposals":true,"enable_documents":true}'
```

Real response:

```json
{
  "message": "Integration configured successfully.",
  "webhook_secret": "QDyV18MCwI1vMl4iMWVICpLYvgPLwG5gTRZtAEQ9S8E",
  "api_secret": "LwOMmp6q1-cZHjtCXNVv1j1a1GllRjgM5mvoRwaHwYw",
  "secret_notice": "Store these secrets now — they are shown only once and are required to sign webhook/API requests (HMAC-SHA256)."
}
```

Anything that declares an inbound surface (webhook or two-way API) gets its
HMAC secret provisioned here, Fernet-encrypted with the instance id as
context binding — the UUID in the URL identifies, it never authenticates.

## 6. Sync, and see the observations

```bash
curl -s "http://127.0.0.1:8000/api/v1/integrations/active?patient_id=$PATIENT_ID" \
  -H "Authorization: Bearer $TOKEN"
# → [{"id": "e6fd2b8f-…", "domain": "dev_dummy", "status": "ACTIVE", "last_synced_at": null, …}]

INSTANCE_ID=<paste the id>

curl -s -X POST "http://127.0.0.1:8000/api/v1/integrations/instance/$INSTANCE_ID/sync?patient_id=$PATIENT_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Real response:

```json
{
  "message": "Sync completed successfully",
  "metrics_synced": 5,
  "pulled": 5,
  "dropped_invalid": 0,
  "status": "success",
  "last_synced_at": "2026-10-18T01:14:06.959206Z"
}
```

One sync of the full-surface dev-dummy instance did all of this:

```bash
curl -s "http://127.0.0.1:8000/api/v1/observations?patient_id=$PATIENT_ID&limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

Real rows (trimmed):

```text
    8480-6  Systolic blood pressure      120.0 mmHg
    8462-4  Diastolic blood pressure     87.0 mmHg
   29463-7  Body weight                  70.4 kg
dev-dummy-mood  Mood (self-reported)         ok
```

Four FHIR observations — and the fifth reading (heart rate `8867-4`) is *not
missing*: heart rate is a telemetry-flagged biomarker, so the engine routed
it to the `telemetry_data` hypertable (TimescaleDB), not the FHIR table.
The same is visible server-side:

```sql
SELECT device_id, slug, value, unit FROM telemetry_data;
--     device_id     |    slug     | value | unit
--  "SDK Quickstart" | heart-rate  |   98  | bpm
```

And the rest of the surface landed too: the demo examination
(`General Examination`, 2026-10-18), the "Influenza-like illness (dev_dummy
demo)" clinical event, the ingested `dev_dummy_demo_report.txt` document
(stamped `source_integration_id`), the auto-applied
`dev_dummy_sleep_quality_score` biomarker proposal, and one queued HITL
concept proposal (`create_concept`, status `proposed`).

## Where next

- [Core Concepts](core-concepts.md) for the nouns you just used, then
  [Authoring & Configuration](authoring-and-configuration.md) to replace
  dev-dummy's synthetic generator with your real source.
- The bridge recipe shows how a *client* pushes data into this same
  pipeline: [Examples → the bridge recipe](examples-and-use-cases.md).
- To clean up: stop the server, then
  `docker rm -f ha-qs-pg ha-qs-redis` and delete the scratch directory.

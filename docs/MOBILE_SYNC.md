# Mobile Sync — Android Companion App

The Health Assistant Android companion app keeps your self-hosted health
records platform in sync with the health data already on your phone. It reads
from Android **Health Connect** (heart rate, steps, weight, SpO₂, sleep),
pushes the readings through the **Health Assistant Bridge** integration, and
routes them to the right place — high-frequency vitals land in the telemetry
store; point-in-time measurements become FHIR observations. Open source,
privacy-first, no third-party cloud: your data moves between your phone and
your server, never anywhere else.

> The app lives in a sibling repo (`app/`). The Bridge integration, the Kotlin
> SDK, and the backend push path live in this repo (`core/`). This doc covers
> the sync architecture; the wire contract is in
> [`integrations/health_assistant_bridge/docs/`](../integrations/health_assistant_bridge/docs/).

---

## How it connects

The app treats **one Bridge integration instance as its single connection
identity**: one base URL, one instance UUID, one optional HMAC secret, bound to
**one patient** on the server. The app never holds the user's login token.
Onboard by scanning a QR code, pasting a connection code, or entering the
details manually; the app probes `GET /status`, stores the credential in
encrypted storage (Keystore-backed), and enters the app.

Everything — metric push *and* reads — flows through the Bridge's two-way API
proxy. Every mutating/reading path is HMAC-signed (`METHOD\n<path>\n<timestamp>
\n<raw_body>`); `GET /status` is the sole unsigned probe.

---

## The sync pipeline

```
Health Connect ──▶ HealthConnectSource ──▶ HealthSyncPipeline ──▶ SQLite outbox
                  (1-day walking window)   (map RawSample →          (/sync items)
                                            bridge ClientRecord)            │
                                                                              ▼
                          SyncCoordinator.drain() → BridgeSyncSender → POST /sync
                          (1000-record grouped batches, looped + chained)      │
                                                                              ▼
                                   Backend: FHIR Observation + telemetry split (is_telemetry)
```

- **Pluggable sources.** A `HealthDataSource` interface is the plugin contract.
  `HealthConnectSource` (reads the 5 HC record types) is the first
  implementation; a `ManualEntrySource` stub demonstrates the plugin path.
  Future sources (Fitbit, Withings, manual entry) register identically.
- **Offline-first outbox.** Every reading is persisted to a SQLite outbox
  *before* it's sent. The `SyncCoordinator` drains it in 1000-record batches,
  groups `/sync` records into one payload, retries transient failures with
  full-jitter backoff, and dead-letters permanent ones. State machine:
  `PENDING → IN_FLIGHT → SYNCED | DEAD_LETTER`. Retries are idempotent (a
  client UUID on every item; the backend dedups).
- **Read-before-drain, in a loop.** The `SyncWorker` reads Health Connect, then
  drains the outbox in a ~5-minute loop, reporting live progress each batch. If
  items remain it chains another pass (`ExistingWorkPolicy.APPEND_OR_REPLACE` —
  it never cancels the in-flight run). "Sync now" uses `KEEP`, so tapping it
  while a sync is running is a no-op.
- **Cursors + history window.** Each data type resumes from a per-type cursor
  (the last successful read), so only **new** readings are pulled after the
  first sync. The first sync's backfill is bounded by a selectable history
  window — **last 7 days by default** (also 30 days, 3 months, 1 year, or all
  history) — and reads one day at a time so a single pass never floods memory.

The backend routes by biomarker: high-frequency vitals (heart rate, steps,
SpO₂) are flagged `is_telemetry` and land in the TimescaleDB telemetry store;
point-in-time measurements (weight) become FHIR observations. The data lands
exactly where the web app's charts and analytics read from.

---

## Reading data back

The same Bridge connection reads the data the app pushed (and everything else
the bound patient has). Two read paths serve the app's native Home cards and
Insights charts:

- **`GET /observations/latest?limit=`** — the latest value **per biomarker**,
  FHIR observations and telemetry merged into one list (newest first). A dense
  heart-rate series never crowds out the other biomarkers.
- **`GET /observations?biomarker=&since=&until=`** — the time series for one
  biomarker. `biomarker` accepts the LOINC code or the slug. Telemetry-flagged
  biomarkers (heart rate, steps, SpO₂) are served from the telemetry store; all
  others from FHIR observations. Same response shape for both sources.

`GET /biomarkers?limit=` enumerates the patient's full biomarker catalog —
telemetry and instance-only lab biomarkers alike — with unit, reference range,
and `is_telemetry` so the app can drive Home/Insights from it instead of a
hardcoded list of data types.

Every observation row carries the same fields regardless of source:
`effective_datetime`, `code`, `raw_value`/`normalized_value`/`normalized_unit`,
a flat `{low, high}` `reference_range`, `interpretation`, `relative_score`,
`biomarker_id`, `biomarker_slug`, and `biomarker_value_type` (STATE biomarkers
also carry `value_string` / `value_codeable_concept`). Telemetry rows synthesize
this shape from the biomarker definition, so a client never branches on source.

---

## Control & observability

Two native Compose screens give you full control over what syncs and how it's
doing:

- **Sync settings** — toggle the Health Connect source, pick which data types
  sync (each requested with the least Health Connect privilege needed), choose
  the sync frequency, and set how far back the first sync reads. Advanced
  options include background reads (Android 14+), the battery-optimization
  whitelist, and a "reset cursors" action.
- **Monitoring** — a live progress bar with throughput and ETA, per-biomarker
  synced/failed counts (in a details popup), the latest reading observed per
  type, the outbox snapshot, a dead-letter list with per-item retry, and a
  "clear outbox" action for when a backlog is unwanted.

---

## Privacy & security

- **Single credential, patient-scoped.** The app holds `base_url` +
  `integration_id` + optional `api_secret` — never the user's login token. The
  Bridge instance is bound to one patient, and every backend read path filters
  by `integration.patient_id`.
- **At rest.** Credentials are stored in `EncryptedSharedPreferences`
  (AES-GCM values, AES256-SIV keys, Keystore-backed master key).
- **In transit.** HMAC-SHA256 on every signed path with a ±5 min replay window.
  Enforce HTTPS off-LAN.
- **No third-party cloud.** Data moves between your phone and your server only.

---

## Requirements & device notes

- **Android 8+ (API 28).** Health Connect is requested at runtime, per data
  type, with least privilege.
- **Health Connect must be installed** (it's built into Android 14+; on earlier
  versions the app offers the Play Store install flow).
- **MIUI / Xiaomi devices:** enable **Install via USB** and **USB debugging
  (Security settings)** in Developer Options, or installs are blocked. Some
  MIUI builds also restrict background activity launches — if the app doesn't
  come to the foreground from a deep link, check the battery/autostart settings.

## See also

- [Integrations Framework](INTEGRATIONS_FRAMEWORK.md) — how providers plug into the platform.
- [Integrations SDK](INTEGRATIONS_SDK.md) — build a custom provider (the SDK the Bridge uses).
- [API Access Layers](API_LAYERS.md) — the three API surfaces, incl. the bridge proxy.
- [`integrations/health_assistant_bridge/docs/`](../integrations/health_assistant_bridge/docs/) — the bridge wire contract.

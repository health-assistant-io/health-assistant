# Health Assistant Bridge — Kotlin SDK

Kotlin client for the Health Assistant **Bridge** integration — the
signed two-way API (`HMAC-SHA256`). Mirrors
`../python-sdk/health_assistant_bridge/` (signing, full-jitter retry,
models); consumed by the first-party Android companion app and any
Kotlin/JVM client.

## Install

Currently JVM; converts to Kotlin Multiplatform (iOS target) later.
Consume it as a composite build from a neighboring Gradle project:

```kotlin
// settings.gradle.kts
includeBuild("../core/integrations/health-assistant-bridge/kotlin-sdk")
```
```kotlin
// build.gradle.kts
dependencies {
    implementation("io.healthassistant:kotlin-sdk:0.6.0")
}
```

## Configure the three credentials

From your server's Bridge card (**Integrations → Health Assistant
Bridge** → "Show pairing code" → `base_url|integration_id|api_secret`):

| Parameter | What it is |
|---|---|
| `baseUrl` | Your server's base URL (no trailing slash) |
| `integrationId` | The bridge **instance** UUID — bound to one patient |
| `apiSecret` | The per-instance HMAC secret, shown **once** at creation; signs every data route (unsigned traffic is rejected) |

## Signed first call

```kotlin
import io.healthassistant.bridge.BridgeClient

val client = BridgeClient(
    baseUrl = "http://127.0.0.1:8000",
    integrationId = "27f13fd9-a577-40a1-a8ee-f0e096162c4d",
    apiSecret = "a-very-long-random-secret",
)

val status = client.getStatus()   // signed → full discovery payload
println(status.status)
// client.close() when you're done (real coroutine usage: runBlocking { ... })
```

Signed `/status` returns the full payload (cursor, `latest_sdks`,
`server_version`, `bridge_features`); unsigned it is the minimal
`{status, server_time}` liveness probe.

## First push — the Universal Data Contract

```kotlin
val resp = client.syncData(
    SyncPayload(
        clientVersion = "0.6.0",
        sourceSystem = "android",
        records = listOf(
            ClientRecord(
                type = "quantitative", code = "8867-4", codingSystem = "loinc",
                name = "Heart Rate", value = 75.0, unit = "bpm",
            )
        ),
    )
)
```

Group lab reports via the `examinations` list — the upstream's stable `id`
is the dedup key, so a re-push is a no-op.

## One patient read

```kotlin
val latest = client.getObservationsLatest()       // ReadEnvelope<ObservationPoint> — dashboard rows
val changed = client.getChangesRaw(since = lastCursor) // one delta poll across six resource types
```

Typed readers cover the bound patient's clinical reads:
`getObservations(...)` / `getBiomarkers()` / `getExaminations()` /
`getExamination(id)` / `listDocuments(...)` (`requestBytes` for binary
content/preview, `getDocumentText` for OCR output). Session-scoped reads
(`getMedications` / `getAllergies` / `getVaccines` / `getClinicalEvents`)
mirror the PWA schemas. Chat helpers: `chat(message, sessionId?)`,
`getMessages(sessionId, limit?)`, `transcribe(audio, mime)`; raw SSE
flows via `requestLines` (`POST /ai/chat/stream`) and `resumeChatStream`.

## Error handling — the typed bridge codes

`requestText` / `requestBytes` / `requestLines` / `chat` throw
`BridgeException` on non-2xx; typed subclasses cover the branch-on-code
vocabulary: `NotFoundException` (404), `TurnConflictException` (409),
`HitlConflictException` (409); everything else decodes
`detail.code` (`validation` 400, `stt_failed` 502). Never
string-match messages; never replay a failed chat turn (start a new one).

## Signing, if you go raw

```kotlin
val headers = Signing.sign(apiSecret, "POST", "/sync", rawBody)
// headers.signature → X-Api-Signature, headers.timestamp → X-Api-Timestamp
```

Canonical form: `METHOD\n<path>[?query]\n<timestamp>\n<raw_body>`
(`path` after the integration id, leading `/`); send the exact bytes you
signed.

## Pinned by tests

- `SchemaConformanceTest.kt` — the client models stay conformant with the
  published sub-API schema (cross-SDK fixtures).
- `BridgeClientTest.kt` — transport behavior against a mock ktor engine.
- `SigningParityTest.kt` — byte-for-byte header parity with the Python
  `sign_request` across ASCII / empty / non-ASCII / GET vectors.

Run: `./gradlew test` (JDK 25). Build: `./gradlew build`.

See also the integration-SDK doc set (background): `docs/dev/sdk/overview.md`.

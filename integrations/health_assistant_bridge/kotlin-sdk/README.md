# Health Assistant Bridge — Kotlin SDK

Kotlin client for the [Health Assistant Bridge](../) two-way API proxy. Mirrors
`../python-sdk/health_assistant_bridge/` (signing, retry/backoff, models).

## Usage

```kotlin
val client = BridgeClient(
    baseUrl = "https://health.example.io",
    integrationId = "<instance uuid>",
    apiSecret = "<per-instance hmac secret — mandatory, shown once at instance creation>",
)

val status = client.getStatus()                       // never signed
client.syncData(SyncPayload(clientVersion = "0.1", sourceSystem = "android",
    records = listOf(ClientRecord(type = "quantitative", code = "8867-4",
        codingSystem = "loinc", name = "Heart Rate", value = 75.0, unit = "bpm"))))

val resp = client.request("GET", "/observations/latest")  // signed when secret set
client.close()
```

## Signing

`/status` is **never** signed. Every other path is HMAC-SHA256 signed over
`METHOD\n<path>\n<timestamp>\n<raw_body>` when `apiSecret` is set. See
[../docs/authentication.md](../docs/authentication.md).

## Status

Phase 1 — `kotlin("jvm")`. Converts to `kotlin("multiplatform")` (ios/android
targets) at Phase 9. HMAC parity verified against the Python SDK
(`src/test/.../SigningParityTest.kt`).

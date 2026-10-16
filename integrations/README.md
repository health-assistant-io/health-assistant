# Bridge client SDK satellites

This folder is the **satellite** corner of `core/` (the repo-layout
umbrella pattern): the standalone client SDKs third-party apps use to
talk to a Health Assistant instance's bridge two-way API. The umbrella
`../integrations/` folder (working copies, examples, explorations such
as MyHealthGovGR) is deliberately NOT a git repo — the canonical,
tested sources of these SDKs ship here, tracked in core.

| SDK | Path | Wall |
|---|---|---|
| Python | `health-assistant-bridge/python-sdk` | `backend/tests/test_bridge_sdk_signing.py`, `test_bridge_sdk_sync_client.py` |
| TypeScript | `health-assistant-bridge/ts-sdk` | `ts-sdk/test/*.mjs` (npm test) |
| Kotlin | `health-assistant-bridge/kotlin-sdk` | `SigningParityTest` (Gradle; needs `JAVA_HOME`/`ANDROID_HOME`) |

The bridge **extension** itself (provider, config flow, manifest, docs)
lives in the extension subsystem home: `backend/app/extensions/builtin/
health_assistant_bridge/` (ADR-0040 D24). Nothing in the backend imports
these SDKs — they are consumer-facing packages with their own packaging
(`pyproject.toml`, `package.json`, Gradle).

Duplication policy, honestly: the umbrella `../integrations/` folder may
hold working copies for local hacking; every canonical change lands here
first (the tests above only run against this copy).

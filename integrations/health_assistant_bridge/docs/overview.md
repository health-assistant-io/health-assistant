# Bridge Overview

The **Health Assistant Bridge** is a general-purpose integration that connects headless clients — browser extensions, mobile apps, backend scrapers — to a Health Assistant instance. It's the documented surface for ingesting medical data scraped from portals that don't offer open APIs.

## Why this exists

Many national health systems (NHS, …) and proprietary hospital portals expose a web UI but no API. The browser extension handles the scraping and parsing of unstructured HTML/JSON; the Bridge provides a secure, unified pipeline to map and store that data into the local clinical record. The backend requires **zero hardcoded logic or parsers for specific portals** — the client is responsible for converting data into the Bridge's [Universal Data Contract](api-reference.md).

## Features

- **Adapter-pattern architecture** — no per-portal backend code; the client owns the scraping and shaping.
- **AI-powered ontology mapping** — the `/map` endpoint aligns raw portal terms ("Natrium", "HCT") to standardized biomarker definitions via an LLM, so a push lands on the right history. See [AI Ontology Mapping](mapping.md).
- **Multiple profiles** — one bridge instance per patient (yourself, a child, …). Each gets a unique instance URL whose `integration_id` is bound to a specific patient — the client never needs national IDs.
- **Official SDKs** — strictly-typed [Python](client-setup.md#python) (sync + async) and [TypeScript](client-setup.md#typescript) clients with timeout, retry, and HMAC signing built in.
- **HMAC security** — optional per-instance `api_secret` signs `/map` and `/sync` with a replay-protected HMAC. See [Authentication](authentication.md).

## The client workflow at a glance

```
1. GET /status      → retrieve the sync cursor (last timestamp ingested)
2. Scrape & extract → pull data newer than the cursor from the portal
3. POST /map         → ask the AI to resolve raw metric names to biomarker IDs
4. (user confirms)   → the client caches the mappings locally
5. POST /sync         → push the mapped records (flat or grouped examinations)
                     → update the cursor for the next cycle
```

The whole loop is idempotent — re-pushing the same history is a no-op as long as you pass the upstream's stable ids. See [Deduplication & Idempotency](api-reference.md#deduplication--idempotency).

## What the bridge does and doesn't do

| Capability | Supported | Notes |
|---|---|---|
| Push (client → HA) | ✅ | `/sync` — observations + examinations |
| Pull (HA ← client) | ❌ | `pull_data` returns `[]`; the bridge is push-only |
| Custom actions | ✅ | "Connection Details", "Reset Sync Cursor" |
| AI mapping | ✅ | `/map` — LLM maps raw metric names to biomarkers |
| HMAC auth | ✅ | Optional `api_secret` config field |
| Notifications | ❌ | Not opted into `supports_notifications` |
| Tools (chat) | ❌ | Not opted into `supports_tools` |
| Clinical events | ❌ | Not opted into `supports_clinical_events` |
| Catalog proposals | ❌ | Not opted into `supports_catalog_proposals` |
| Documents | ❌ | Not opted into `supports_documents` |

## See also

- [Authentication & Security](authentication.md)
- [Client SDK Setup](client-setup.md)
- [API Reference](api-reference.md)
- [AI Ontology Mapping](mapping.md)
- [Troubleshooting](troubleshooting.md)
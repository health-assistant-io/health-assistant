# Overview

The **FHIR Server** integration connects Health Assistant to an external FHIR R4 server — a hospital (Epic/Cerner), a personal health record, or a local [HAPI FHIR](https://hapiproject.org/) — and syncs the **full patient record** both ways. It pulls remote results into your local record and pushes local observations back out.

## What it does

- **Two-way sync** with any FHIR R4 server, on a per-patient basis.
- **Pulls the full record** — not just observations. Labs and vitals, conditions, encounters, documents (with OCR), medications, allergies, and immunizations all land through the same canonical write paths the UI uses (RBAC, audit, dedup).
- **Pushes observations back out** via FHIR conditional update, idempotent per local UUID.
- **SMART-on-FHIR standalone launch** with Dynamic Client Registration (no client ID to manage) for hospitals, plus a tokenless mode for open servers.
- **Finds the right remote patient** with a searchable picker that auto-suggests by MRN.
- **Proposes new biomarker definitions** for remote codes your catalog doesn't know yet — queued for your approval (the AI never writes clinical data directly).

## What it does NOT do

- It does **not** push Conditions, Encounters, or other non-Observation resources outbound. Hospitals rarely accept externally-written clinical data; the Observation push covers the "share my data back" case. (See [Push](push.md).)
- It does **not** support multi-patient / hospital-wide ingest. The integration is per-patient (one `UserIntegration` row per person); a batch backfill is a different shape.
- It does **not** do mTLS, Basic-auth, or API-key auth to the remote server. SMART + tokenless covers the common cases; other auth modes are added only when a real server requires them.

## Authorization at a glance

| Mode | Use case | Authorize step? |
|------|----------|-----------------|
| **SMART** (default) | Hospitals, Epic/Cerner, the SMART Health IT sandbox | Yes — click **Authorize** after saving; the instance is `PENDING` until the callback stores encrypted tokens |
| **None / tokenless** | Local or open FHIR servers (e.g. a local HAPI FHIR in Docker) | No — goes straight to `ACTIVE`; operates without a token |

See [Authorization & Connection](connection.md).

## Sync direction

`sync_direction` controls what the **scheduled** sync and the platform **Sync Now** button do:

| Value | Behaviour |
|-------|-----------|
| `both` (default) | Pull remote data in **and** push local observations out |
| `pull_only` | Only pull from the FHIR server into Health Assistant |
| `push_only` | Only push local observations to the FHIR server |
| `none` | No automatic sync — use the action buttons manually |

The **Pull Now** / **Push Now** action buttons always run regardless of this setting, for explicit manual control.

> Switching to `both` or `push_only` requires re-authorizing so the SMART consent screen can request write permissions (`patient/*.write`).

## See also

- [Authorization & Connection](connection.md)
- [Pull — Full Patient Record](pull.md)
- [Push — Outbound Observations](push.md)
- [Selecting the Remote Patient](patient-selection.md)

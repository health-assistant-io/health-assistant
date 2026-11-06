# Overview

The **FHIR Client** integration connects Health Assistant to an external
[FHIR R4](https://hl7.org/fhir/R4/) server — a lab portal's FHIR endpoint,
a regional health information exchange, a research data warehouse, or any
other server that issues you a static access token. It **pulls** the bound
patient's record into Health Assistant on every sync.

## Mental model

- One instance = one remote server + one bound remote patient. Every
  search the connector runs is scoped to that patient; it never pulls
  unscoped.
- The access token is a credential, not configuration: it is sealed into
  the encrypted credential store and never appears in the instance's
  configuration or the UI.
- Pulled records flow through the same canonical pipelines as every other
  integration — nothing writes around the platform.

## When to use it

- The remote server authenticates with a **static bearer/API key** you can
  paste into a form.
- You want a **read-only** copy of the remote record (labs, observations,
  medications, visits) inside Health Assistant.

## When NOT to use it

- **Your hospital speaks SMART-on-FHIR** (Epic, Cerner, the SMART Health
  IT sandbox) and you also want to push observations back — use the
  **FHIR Server (Hospital Sync)** integration instead; it owns the OAuth
  round-trip and the push pipeline. This connector deliberately does not
  implement SMART authorization.
- The source is your own mobile app or backend client — use the
  **Health Assistant Bridge**.
- The source pushes events to you — use the **Universal Webhook**.

## What it pulls

| Remote resource | Lands in |
|---|---|
| Observation | Observations / Biomarker Engine |
| DiagnosticReport | Labs |
| MedicationStatement | Medications |
| Encounter | Examinations (visits) |

See [Pull](pull.md) for cursors, dedup, and how unmapped codes are handled.

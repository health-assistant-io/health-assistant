# Overview

The **Withings** integration connects Health Assistant to a Withings
account — a Body scale, a blood-pressure monitor, a sleep tracking
mat or an activity tracker. It **pulls** the account's measurements on
every sync through OAuth2: you authorize your own Withings partner app
once, and the connector keeps the tokens fresh afterwards.

## Mental model

- One instance = one Withings partner app + one authorized account. The
  account binding (the Withings user id) is recorded after the consent
  round-trip.
- The client secret and both tokens are credentials, not configuration:
  they are sealed into the encrypted credential store and never appear in
  the instance's configuration or the UI.
- The access token **refreshes before every pull** — a sync never runs
  with a stale token, and a broken credential stops the sync instead of
  producing empty results.
- Pulled measurements flow through the same canonical pipelines as every
  other integration — nothing writes around the platform.

## What it pulls

| Withings data | Lands in |
|---|---|
| Weight, body composition (fat %) | Telemetry store (Biomarker Engine) |
| Blood pressure (systolic, diastolic) | Telemetry store |
| Heart rate, SpO2 | Telemetry store |
| Temperature (body) | Telemetry store |

Measurements whose type has no catalog mapping are still stored — as raw
observations with the gap recorded — never dropped.

## What it deliberately does not pull (yet)

**Sleep and activity series** (night summaries, steps, workouts) are not
wired: the platform has no catalog mappers for them yet. Flagging the gap
beats half-wiring a stream that lands nowhere.

## When NOT to use it

- You want hospital two-way sync (read **and** write) — use the **FHIR
  Server (Hospital Sync)** integration.
- The source pushes events to you — use the **Universal Webhook**.
- The source is your own mobile app — use the **Health Assistant Bridge**.

See [Pull](pull.md) for cursors, dedup and canonical units.

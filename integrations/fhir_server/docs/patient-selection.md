# Selecting the Remote Patient

The integration is **per-patient**: it syncs exactly one local Health Assistant patient with one remote FHIR patient. This page explains how each side is determined and how to pick the right remote patient.

## The local side

When you create the integration, the **currently selected patient** is the sync target — their id is stored on the integration row (`UserIntegration.patient_id`). All pulled data lands in that patient's record; all pushed observations are sourced from it. The local side is therefore always deterministic: pick the patient in the app, then add the integration.

## The remote side

The remote FHIR patient is resolved in **priority order**:

1. **Find Patient picker** (recommended) — search the server and click the match. Stored as `remote_patient_id`.
2. **Manual entry** — the optional *Remote FHIR Patient ID* config field (set by the picker, or typed directly).
3. **SMART launch token** (fallback for SMART mode) — the patient resolved by the hospital's consent screen during Authorize, when no explicit id is set.

An explicit `remote_patient_id` (set via the picker or manual entry) always wins — it lets you override the SMART-resolved patient or supply one for a tokenless server.

## The Find Patient picker

The **Find Patient** action on the integration detail page opens an interactive picker:

- **On open, it auto-suggests** — it reads the local patient's MRN and searches the remote server by that identifier. When the MRNs match, the right remote patient surfaces with zero typing. If there's no MRN, it falls back to the local patient's name.
- **Live search** — type any name or identifier; the picker queries the server (`GET /Patient?name=…` or `?identifier=…`) and lists matches with their name, MRN, DOB, and gender.
- **Click to select** — choosing a match sets `remote_patient_id` and refreshes the detail page. The currently-linked patient is highlighted in the list.

The picker degrades gracefully: if the integration is still `PENDING` (SMART, not yet authorized) or the server is unreachable, it shows an empty result list with a hint rather than erroring.

## When you don't need the picker

- **SMART mode** — the hospital's consent screen has you pick the patient during Authorize. That patient is stored in the token and used automatically. Use the picker only if you want to **override** it (e.g. the wrong patient was selected at consent).
- **MRN already matches** — open the picker and the auto-suggest surfaces the match; one click confirms.

## When the picker is essential

- **None / tokenless mode** — there's no SMART token to resolve a patient, so you **must** set the remote patient (via the picker or manual entry). Without it, pulls run unscoped — pulling every patient's data or getting rejected, depending on the server.

## See also

- [Authorization & Connection](connection.md)
- [Pull — Full Patient Record](pull.md)

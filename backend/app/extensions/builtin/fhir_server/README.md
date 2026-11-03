# FHIR Server (Hospital Sync)

Connect an external FHIR R4 server — a hospital (Epic/Cerner), a personal health record, or a local [HAPI FHIR](https://hapiproject.org/) — and sync the **full patient record**: pull remote labs, vitals, conditions, encounters, documents, medications, allergies, and immunizations into Health Assistant, and push local observations back out.

See [`docs/`](docs/) for the full guide. The `docs/docs-tree.json` drives the in-app documentation modal.

## Quick start

1. **Admin:** enable the `fhir_server` integration (`/admin/system/integrations`).
2. Add the integration on a patient, enter the **FHIR Base URL** (e.g. `https://r4.smarthealthit.org` or `http://localhost:8095/fhir`), and pick an **Authorization** mode:
   - **SMART** — for hospitals / Epic / Cerner / the SMART Health IT sandbox. Save, then click **Authorize** to run the standalone-launch round-trip (Dynamic Client Registration — no client ID needed). The instance is `PENDING` until the callback stores the encrypted tokens.
   - **None / tokenless** — for local or open servers (e.g. a local HAPI FHIR). No authorize step; goes straight to `ACTIVE`.
3. For tokenless mode, **select the remote patient** — click **Find Patient** (auto-suggests by MRN) or enter a *Remote FHIR Patient ID*. See [Selecting the Remote Patient](docs/patient-selection.md).
4. Pick the **Record Types to Pull** (default: the full record) and a **Sync Direction**.
5. Use **Check Connection** to verify, **Pull Now** / **Push Now** for manual control.

> Vanilla HAPI FHIR does **not** serve `/.well-known/smart-configuration` — use **None** mode for it.

## Highlights

- **Full-record pull** — `Observation` (→ Biomarker Engine), `Condition` (→ Health Journeys), `Encounter` (→ Examinations), `DocumentReference` (→ OCR), `MedicationStatement`/`MedicationRequest` (→ Medications), `AllergyIntolerance`, `Immunization`. Per-resource `_lastUpdated` cursors; DB-layer dedup.
- **Idempotent push** — conditional update keyed by the local UUID, subject rewritten to the remote patient, echo exclusion, best-effort remote Provenance.
- **Find Patient picker** — search the server by name/MRN and click to link; auto-suggests by the local patient's MRN.
- **Catalog proposals** — unmapped remote LOINC/SNOMED codes queue for your review; approve to define the biomarker. AI proposes, you approve.

## See also

- [Overview](docs/overview.md)
- [Authorization & Connection](docs/connection.md)
- [Pull — Full Patient Record](docs/pull.md)
- [Push — Outbound Observations](docs/push.md)
- [Selecting the Remote Patient](docs/patient-selection.md)
- [Troubleshooting](docs/troubleshooting.md)

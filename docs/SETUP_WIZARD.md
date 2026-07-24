# Setup Wizard (Guided Onboarding)

Health Assistant adds a modular, in-app **guided setup** system. It serves three audiences:

1. **First-run role setup** — a step-by-step checklist for **System Admins**, **Tenant Admins**, and **Simple Users** (each role sees the steps relevant to it). The wizard is **reopenable** — you can come back later to finish, or to re-checklist after a major change.
2. **Patient setup** — a guided, "advanced form" wizard covering the full patient profile: demographics, contact points, address, **race / ethnicity**, **preferred language**, **insurance provider**, allergies, current medications, and current clinical events (e.g. an active pregnancy or chronic pain journey). It is the same surface as the patient's detail page, just step-organized.
3. **Doctor / Organization setup** — analogous advanced wizards (planned).

---

## 1. The contract: `GET /api/v1/setup/checklist`

The wizard is driven by a single backend-derived endpoint:

```
GET /api/v1/setup/checklist
GET /api/v1/setup/checklist?entity=patient&entity_id=<uuid>
```

Response:
```json
{
  "role": "ADMIN",
  "entity": "patient",
  "entity_id": "…",
  "steps": [
    {"id": "tenant.first_patient", "title_i18n_key": "setup.steps.tenant.first_patient",
     "kind": "redirect", "completed": true,  "optional": false,
     "payload_hint": {"route": "/patients?new=patient"}},
    {"id": "patient.allergies",   "title_i18n_key": "setup.steps.patient.allergies",
     "kind": "redirect", "completed": false, "optional": true,
     "payload_hint": {"route": "/patients/…/allergies"}}
  ],
  "completion": 0.6
}
```

- **`role`** — the role portion of the checklist (always returned). The `USER` role gets the smallest set (set language + link self-patient); `ADMIN`/`MANAGER` get tenant-buildout steps; `SYSTEM_ADMIN` gets cross-tenant steps.
- **`entity` / `entity_id`** — when supplied, extra steps for that specific entity (today: `patient`) are merged in.
- **`steps[].kind`** drives the UI step component:
  - `redirect` — the step links elsewhere (`/patients?new`); the wizard re-polls the checklist on return.
  - `inline_form` — the wizard renders a section inline (e.g. contact / telecom).
  - `external_config` — deep-link into a settings sub-page (e.g. AI config → `/admin/ai` or `/settings/preferences`).
  - `derived` — read-only evaluation; the wizard shows status + hint.
- **`completion`** — ratio of *mandatory* steps complete (optional steps don't count — "no allergies" is a valid end-state). 0.0–1.0.

---

## 2. Why backend-derived (no wizard-state table)

The checklist state is **computed from the live data**, not stored. The wizard serves as a reflection of what is already there. This means:

- **No drift** — add an allergy and the checklist step flips green on the next read, with no explicit "I completed this step" write.
- **Always reopenable** — re-open the wizard any time; the state is never "first-run-locked."
- **One source of truth** — patient setup completion == patient data completeness. There is no separate `onboarding_state` row that can disagree with the rows that actually describe the patient.

A *future* iteration may add an explicit **dismissed-steps** list (stored in `users.settings.setup_wizards.dismissed_steps`) so a user can skip steps they consider inapplicable ("no allergies"). That will layer on top of derived checks, not replace them. See `dev/audits/setup-wizard-design.md` §D2 for the rationale.

---

## 3. Patient profile fields covered

The wizard covers the full patient profile. The model previously held only `name / gender / birthDate / mrn` in the quick-create form. The wizard uses columns that already existed (`address`, `telecom`, `emergency_contact`) and adds a new **FHIR R4 `extensions` JSONB** column on `fhir_patients` for the FHIR-extension-shaped demographics US Core specifies:

| Field | FHIR shape | Storage |
|---|---|---|
| Race | US Core race extension (`urn:oid:2.16.840.1.113883.4.642.40.46\|race`, complex `ombCategory` + `text`) | `extensions['race']` |
| Ethnicity | US Core ethnicity extension (same shape) | `extensions['ethnicity']` |
| Preferred language | IHE PCD extension (`urn:oid:1.3.6.1.4.1.19376.1.5.3.1.4.51`, `valueCode`) | `extensions['preferred_language']` |
| Insurance provider | HA custom (`urn:healthassistant:insurance-provider`, `valueString`) | `extensions['insurance_provider']` |

The supported-extension registry lives in `backend/app/services/fhir_extensions.py`. `Patient.to_fhir_dict()` projects the local map onto a canonical FHIR `extension[]` array (round-trips through the FHIR R4 facade). Adding a new supported extension = one entry in `SUPPORTED_PATIENT_EXTENSIONS` — the model, schema, and validation all reuse that registry.

> **Insurance** is intentionally a simple `string` extension for v1. A proper FHIR `Coverage` resource is roadmap — the migration path is documented in `dev/audits/setup-wizard-design.md` §D3 + §5-Q2.

The remaining wizard sections are aggregations of existing patient-instance records, not new model fields:

- **Allergies** → ≥1 `AllergyIntolerance` row (active or historic).
- **Current medications** → ≥1 active `Medication` row (`status = ACTIVE`).
- **Current pains / events** → ≥1 `ClinicalEvent` row (active episodes — e.g. chronic migraines, pregnancy; "pains" is not a separate table, it is a journey in the existing Clinical Events system).

---

## 4. Adding a step (developer guide)

Steps are pluggable evaluators in two registries in `backend/app/services/setup_checklist_service.py`:

- `ROLE_CHECKLISTS: Dict[Role, tuple[StepEvaluator, ...]]` — the role portion.
- `ENTITY_CHECKLISTS: Dict[str, tuple[StepEvaluator, ...]]` — the per-entity portion.

Each `StepEvaluator` is:

```python
async def my_evaluator(db, user: TokenData, scope: dict) -> StepResult:
    # scope carries tenant_id, user_id, and (for entity evaluators)
    # 'patient' / 'doctor' / 'organization' loaded model instances.
    completed = ...  # boolean derived from live data
    return _step(
        "<step_id>",            # stable id
        "setup.steps.<id>",      # i18n key
        "redirect"|"inline_form"|"external_config"|"derived",
        entity="patient",        # None for role steps
        completed=completed,
        optional=True/False,
        payload_hint={"route": "..."},
    )
```

Add it to the right registry tuple and commit. The endpoint, schema, and (later) frontend pick it up automatically because the wizard is data-driven.

### RBAC

Patient-entity checklist steps run after the standard `check_patient_access` helper (`USER` role can only checklist patients linked to themselves; `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` see tenant-scoped). No new auth surface — the wizard reuses the existing tenancy + patient-scoping discipline (see [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md)).

---

## 5. Iteration roadmap

This branch ships **iteration 1** of the design (`dev/audits/setup-wizard-design.md`):

| Iter | Scope | Status |
|---|---|---|
| 1 | Backend: patient `extensions` column + Alembic migration + `fhir_extensions.py` registry + schema updates; role-aware `SetupChecklistService` skeleton; `GET /setup/checklist` endpoint; tests. | shipped |
| 2 | Frontend: `/patients/:id/setup` wizard route consuming the checklist; completion-card on `PatientDetail`; patient form sections for address/telecom/emergency_contact now visible; extensions rendered. | planned |
| 3 | Role wizards for System Admin / Tenant Admin / User; "resume setup" entrypoint in the user menu. | planned |
| 4 | Doctor advanced wizard + checklist evaluators; Organization advanced wizard; fix the `org_type` never-set bug on the existing create form. | planned |
| 5 | Optional `dismissed_steps` persistence in `users.settings`; typed `UserPreferencesResponse` schema. | planned |

See `dev/audits/setup-wizard-design.md` for the full design rationale, decisions record, and open questions.
# Setup Wizard (Guided Onboarding)

Health Assistant has a modular, in-app **guided setup** system that walks every user through initial configuration — and stays available for later reconfiguration. It is **backend-derived** (completion is computed from live data, not stored in a state table) and **always reopenable** (no first-run-only gate).

## How it works — the 30-second mental model

1. The backend computes a **checklist** of steps via `GET /api/v1/setup/checklist`. Each step has a `completed` bit derived from the actual database state (e.g. "does a patient exist?", "does an AI provider exist?").
2. The frontend renders this checklist as a **wizard** — either a full-page route (`/setup/wizard`) or a persistent side drawer (the popup that minimizes to a floating badge).
3. Each step **guides the user** to the right settings page (e.g. "Create a patient" → opens `/patients?new=patient`). The user configures in the real UI — **no duplicated forms inside the wizard**. When they return, the wizard re-checks the checklist and the step flips green.

This means the wizard is a **guided tour**, not a parallel configuration surface. Everything is configured in its canonical location (the AI settings page, the patient create modal, the user management page). The wizard just tracks whether you've done it.

---

## Two wizard surfaces

### 1. Role wizard (`/setup/wizard`)

A per-account checklist based on the user's role:

| Role | Steps |
|---|---|
| **USER** | Set language · Link your patient record |
| **ADMIN / MANAGER** | Create org · Create patient · Add doctor · Configure AI · Invite member |
| **SYSTEM_ADMIN** | Create tenant · Seed catalog · Create patient · Configure system AI · Add user |

**Entrypoints:**
- **User menu** (Header dropdown) → "Setup" item.
- **`NoPatientState`** empty-state → "Run setup wizard" gradient CTA (replaces the old "coming soon" placeholder).
- **Direct URL** — `/setup/wizard` is a permanent route.

**Full-page mode:** the wizard shows a two-pane layout — a vertical stepper on the left (with a progress ring) and the active step's content on the right. The content depends on `step.kind` (see §Step kinds below).

### 2. Patient wizard (`/patients/:id/setup`)

A per-patient profile-completion wizard covering: birth date, address, telecom, emergency contact, race/ethnicity, preferred language, insurance provider, allergies, current medications, and clinical events. Accessible from the **SetupChecklistCard** on the patient detail page (`/patients/:id`).

---

## The popup drawer (persistent side panel)

When the user clicks "Open" on a step from the full-page wizard, the wizard navigates to the target page **and** opens a **side drawer** that persists across navigation:

- **Non-blocking on desktop** — no dark backdrop; the user interacts with the settings page underneath.
- **Accordion cards** — each step is a collapsible card. Clicking one expands it (showing description, sub-step progress, Recheck + Open buttons) and collapses the others.
- **Minimize** — the PanelRightClose button collapses the drawer to a floating progress pill at the bottom-right; one click to expand back.
- **Click-outside** — clicking outside the drawer auto-collapses it to the floating pill.
- **Floating badge** — when the drawer is closed, a blue floating button appears at bottom-right (only if the user has explicitly started the wizard and setup is incomplete). It can be **dismissed** (stops nagging until the wizard is reopened).
- **Auto-recheck** — on window focus, the drawer re-polls the checklist. The user completes the action on the target page, returns, and the step is already green.
- **Bottom bar** — two buttons: "Open full wizard" (navigates to `/setup/wizard`) and "Exit wizard" (fully closes + deactivates the floating badge).

### Minimize from the full-page wizard

The full-page wizard (`/setup/wizard`) has a **minimize button** (PanelRightClose, in the footer) that navigates to the dashboard + opens the drawer in collapsed floating-pill mode — same minimize UX as the drawer's own minimize.

---

## The backend contract

### `GET /api/v1/setup/checklist`

```
GET /api/v1/setup/checklist
GET /api/v1/setup/checklist?entity=patient&entity_id=<uuid>
```

Returns a `SetupChecklistResponse`:

```json
{
  "role": "ADMIN",
  "entity": null,
  "entity_id": null,
  "steps": [
    {
      "id": "tenant.first_patient",
      "entity": null,
      "title_i18n_key": "setup.steps.tenant.first_patient",
      "kind": "redirect",
      "completed": false,
      "optional": false,
      "payload_hint": {"route": "/patients?new=patient"}
    },
    {
      "id": "tenant.ai_config",
      "entity": null,
      "title_i18n_key": "setup.steps.tenant.ai_config",
      "kind": "external_config",
      "completed": false,
      "optional": true,
      "payload_hint": {
        "sub_steps": [
          {"id": "provider", "done": false, "route": "/admin/tenant/ai-config?tab=providers"},
          {"id": "model", "done": false, "route": "/admin/tenant/ai-config?tab=models"},
          {"id": "assignment", "done": false, "route": "/admin/tenant/ai-config?tab=tasks"}
        ]
      }
    }
  ],
  "completion": 0.25
}
```

- **`role`** — always returned; drives which role-step set is included.
- **`entity` / `entity_id`** — when supplied (e.g. `entity=patient`), per-entity steps are merged in.
- **`completion`** — ratio of *mandatory* steps complete (optional steps don't count). 0.0–1.0.

### `GET /api/v1/setup/extension-catalog`

Returns the supported FHIR R4 extension catalog for patient demographics (race, ethnicity, preferred language, insurance provider) + the CDC OMB code picklists. The patient wizard's extensions section is data-driven from this endpoint — no hardcoded CDC codes on the client.

---

## Step kinds

Each step's `kind` field drives the frontend renderer:

| Kind | What it means | Frontend renderer |
|---|---|---|
| `redirect` | The step links to a page where the user performs the action (e.g. "Create a patient" → `/patients?new=patient`). No form is duplicated inside the wizard. | `RedirectStep` — a CTA button ("Open" when incomplete, "Manage" when complete). |
| `external_config` | Deep-link to a settings sub-page. Same as `redirect` but semantically "configuration". When `payload_hint.sub_steps` is present, renders as a guided multi-step redirect (see below). | `RedirectStep` or `GuidedExternalStep` (if `sub_steps` present). |
| `inline_form` | The wizard renders a form section inline (patient wizard only — demographics, contacts, extensions). Persisted via `PUT /patients/:id`. | `InlineFormStep` → section registry → `DemographicsSection` / `ContactsSection` / `ExtensionsSection`. |
| `derived` | Read-only evaluation — the wizard shows status + a hint. No user action possible (e.g. "catalog seeded" is automatic). | `DerivedStep`. |

### Guided sub-step redirect (`external_config` with `sub_steps`)

Used by the **AI config** step. Instead of three flat checklist entries, a single `external_config` step carries a `payload_hint.sub_steps` list:

```json
"payload_hint": {
  "sub_steps": [
    {"id": "provider", "done": true, "route": "/admin/tenant/ai-config?tab=providers"},
    {"id": "model", "done": false, "route": "/admin/tenant/ai-config?tab=models"},
    {"id": "assignment", "done": false, "route": "/admin/tenant/ai-config?tab=tasks"}
  ]
}
```

The full-page wizard renders this as a **3-card guided checklist** — each card shows the sub-step name, done/todo status, and an "Open"/"Manage" button that navigates to the real AIConfig page at the right `?tab=`. The popup drawer shows a compact summary ("1 of 3 done") + a single "Open" button to the first incomplete sub-step.

The user configures AI in **one place** (the settings page). The wizard guides them through the three tabs and tracks completion.

---

## Why backend-derived (no wizard-state table)

The checklist state is **computed from the live data**, not stored. This means:

- **No drift** — add an allergy and the checklist step flips green on the next read.
- **Always reopenable** — re-open the wizard any time; the state is never "first-run-locked."
- **One source of truth** — setup completion == data existence. There is no separate `onboarding_state` row that can disagree with reality.

A future iteration may add an explicit **dismissed-steps** list (`users.settings.setup_wizards.dismissed_steps`) so a user can mark steps "not applicable". That will layer on top of derived checks, not replace them.

---

## How to reopen / reconfigure later

The wizard is **never locked**. After initial setup is complete, you can return to any step:

1. Open the **user menu** (Header dropdown) → click **"Setup"** → the full-page wizard at `/setup/wizard`.
2. Or open any **patient detail page** → the **SetupChecklistCard** (sidebar) shows the patient-wizard completion + an "Open setup wizard" button → `/patients/:id/setup`.
3. The popup drawer's floating badge (bottom-right) reappears if you previously started the wizard and setup is incomplete. Dismiss it with the X button; it won't reappear until you open the wizard again.

Inside the wizard, every step — completed or not — shows a **"Manage"** button that reopens the settings page so you can review or change the configuration.

---

## Adding a new step (developer guide)

### Backend — one evaluator + one registry entry

Steps are pluggable evaluators in `backend/app/services/setup_checklist_service.py`:

```python
async def my_evaluator(db, user: TokenData, scope: dict) -> StepResult:
    completed = ...  # boolean derived from live data
    return _step(
        "my_namespace.my_step",     # stable id
        "setup.steps.my_namespace.my_step",  # i18n key
        "redirect",                 # redirect | inline_form | external_config | derived
        completed=completed,
        optional=False,
        payload_hint={"route": "/my-page"},
    )
```

Add it to the right registry tuple:
- `ROLE_CHECKLISTS[Role.ADMIN]` for role steps.
- `ENTITY_CHECKLISTS["patient"]` for patient-entity steps.

The endpoint, schema, and frontend pick it up automatically — the wizard is data-driven.

### Guided sub-steps (like AI config)

For a step that should guide through multiple sub-pages (like AI's provider → model → tasks):

```python
return _step(
    "my.complex_config",
    "setup.steps.my.complex_config",
    "external_config",
    completed=all_done,
    optional=True,
    payload_hint={"sub_steps": [
        {"id": "part_a", "done": part_a_done, "route": "/my-page?tab=a"},
        {"id": "part_b", "done": part_b_done, "route": "/my-page?tab=b"},
    ]},
)
```

The frontend's `GuidedExternalStep` renderer automatically picks up the `sub_steps` and renders the guided checklist — no frontend code needed.

### Frontend — i18n + description

Add the step's title + description to both `frontend/src/locales/en/common.json` and `el/common.json`:

```json
"setup": {
  "steps": {
    "my_namespace": {
      "my_step": "Step Title"
    }
  },
  "step_desc": {
    "my_namespace.my_step": "Explanation shown in the wizard's accordion card."
  }
}
```

For `redirect` / `external_config` / `derived` steps, no frontend component is needed — the existing renderers handle them. For `inline_form` steps, register a section component in `components/setup/sections/registry.ts`.

### RBAC

Patient-entity checklist steps run after `check_patient_access` (`USER` → own patient only; `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` → tenant-scoped). No new auth surface.

---

## Patient extensions (FHIR R4 demographics)

The patient wizard covers US Core demographics via a FHIR R4 `extensions` JSONB column on `fhir_patients`:

| Field | FHIR shape | Storage |
|---|---|---|
| Race | US Core race extension | `extensions['race']` |
| Ethnicity | US Core ethnicity extension | `extensions['ethnicity']` |
| Preferred language | IHE PCD extension | `extensions['preferred_language']` |
| Insurance provider | HA custom | `extensions['insurance_provider']` |

The registry lives in `backend/app/services/fhir_extensions.py`. `Patient.to_fhir_dict()` projects the local map onto a canonical FHIR `extension[]` array. The client renders the extension inputs from `GET /setup/extension-catalog` (data-driven CDC OMB code lists).

---

## File map

### Backend
| File | Responsibility |
|---|---|
| `backend/app/services/setup_checklist_service.py` | The two evaluator registries (`ROLE_CHECKLISTS`, `ENTITY_CHECKLISTS`) + the `SetupChecklistService` + extension-catalog builder. Adding a step = one evaluator + one registry entry. |
| `backend/app/schemas/setup_checklist.py` | `StepResult`, `SetupChecklistResponse`, `ExtensionCatalogItem/Response`. |
| `backend/app/api/v1/endpoints/setup_checklist.py` | `GET /setup/checklist` + `GET /setup/extension-catalog`. |
| `backend/app/services/fhir_extensions.py` | The 4-extension registry + FHIR canonical conversion. |
| `backend/data/seeds/omb_race_ethnicity.json` | CDC OMB race/ethnicity/language picklist seed. |
| `backend/tests/test_setup_checklist_service.py` | 24 tests: step shapes, completion flips, route correctness, sub_steps payload, extension catalog. |

### Frontend
| File | Responsibility |
|---|---|
| `pages/Setup/RoleSetupWizard.tsx` | The full-page role wizard (`/setup/wizard`). Two-pane `SetupLayout` + `StepRenderer`. |
| `pages/Patients/PatientSetupWizard.tsx` | The per-patient wizard (`/patients/:id/setup`). |
| `components/setup/SetupWizardDrawer.tsx` | The persistent popup drawer — accordion cards, minimize-to-badge, click-outside-collapse, floating reopen badge. |
| `components/setup/SetupLayout.tsx` | Two-pane shell (stepper + active step panel). Shared by role + patient wizards. |
| `components/setup/SetupProgressRing.tsx` | SVG circular progress ring. |
| `components/setup/SetupChecklistCard.tsx` | Completion card on `PatientDetail`. |
| `components/setup/Stepper.tsx` | Vertical step list (full-page wizard left pane). |
| `components/setup/steps/StepRenderer.tsx` | Kind dispatcher → RedirectStep / GuidedExternalStep / DerivedStep / InlineFormStep. |
| `components/setup/steps/RedirectStep.tsx` | Simple redirect step (Open/Manage CTA). |
| `components/setup/steps/GuidedExternalStep.tsx` | Multi-sub-step guided redirect (AI config). |
| `components/setup/steps/DerivedStep.tsx` | Read-only status step. |
| `components/setup/sections/` | Inline-form sections (Demographics, Contacts, Extensions) + registry. |
| `components/setup/SubWizardShell.tsx` | Shared inline multi-step container (for future sub-wizards). |
| `store/slices/uiSlice.ts` | `setupDrawerOpen`, `setupDrawerCollapsed`, `setupWizardActive` state. |
| `services/setupService.ts` | `getSetupChecklist()` + `getExtensionCatalog()`. |
| `types/setup.ts` | `SetupStep`, `SetupChecklist`, `StepPayloadHint`, `GuidedSubStep`, `ExtensionCatalog`. |

---

## Iteration status

| Iter | Scope | Status |
|---|---|---|
| 1 | Backend: patient `extensions` JSONB + `fhir_extensions.py` registry + `SetupChecklistService` + `GET /setup/checklist` endpoint + tests. | shipped |
| 2 | Frontend: `/patients/:id/setup` wizard route + completion card on `PatientDetail` + inline-form sections (demographics, contacts, extensions) + `GET /setup/extension-catalog` + CDC OMB seed. | shipped |
| 3 | Role wizard (`/setup/wizard`) + persistent popup drawer (accordion cards, minimize-to-badge, floating reopen) + guided AI sub-step redirect + "Resume Setup" entrypoint in user menu + `NoPatientState` fix. | shipped |
| 4 | Doctor + Organization advanced wizards + checklist evaluators; fix the `org_type` never-set bug on the existing create form. | planned |
| 5 | Optional `dismissed_steps` persistence in `users.settings`; typed `UserPreferencesResponse` schema. | planned |

See `dev/audits/setup-wizard-design.md` for the full design rationale and decisions record.

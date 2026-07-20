# Health Assistant — Clinical Events & Journeys

Health Assistant tracks **clinical events** — longitudinal, multi-visit health journeys that span days, months, or years. A pregnancy, a chronic-pain cycle, a surgical recovery, an ongoing medication regimen: each is a single `ClinicalEvent` row that aggregates related examinations, observations, anatomy links, and discrete episodes into one navigable timeline. This doc is the canonical reference for the seed JSON format, the type-blueprint fields (`schedule_kind`, `metadata_schema`, journey templates), the category system, and how to localize everything.

Clinical events are FHIR `Condition` resources under the hood (exposed via the `/fhir/R4/Condition` facade) — see [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md). For the REST endpoints that create / update / list them, see [API.md](API.md).

## 1. The two-layer model

| Layer | Table | What it holds | Examples |
|---|---|---|---|
| **Type blueprint** | `clinical_event_types` | The *kind* of journey — name, rendering hint, dynamic field schema, journey template. Tenant-agnostic; seeded at startup. | "Pregnancy", "Pain Episode", "Surgical Recovery" |
| **Instance** | `clinical_events` | One patient's specific journey — title, onset/resolved dates, status, the populated dynamic fields, related exam/observation links. Tenant- + patient-scoped. | "Third Pregnancy (2026)", "Chronic Lower Back Pain" |

A type declares the contract; an instance fills it in. The type's `metadata_schema` drives the form fields the user fills out when creating an instance of that type (Phase 4a).

## 2. Seed JSON — envelope and example

Each type is a row in `backend/data/seeds/clinical_event_types.json`. The file uses the standard `{metadata, items}` envelope consumed by `SeedService.seed_clinical_event_types` (stage 5 of the boot-time pipeline — see [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md)).

```json
{
  "metadata": {
    "version": "3.1.0",
    "source": "Health Assistant",
    "last_updated": "2026-07-20",
    "schema_note": "Field descriptors use the typed MetadataFieldType/CatalogType enums. schedule_kind values: state | range | recurring | point."
  },
  "items": [
    {
      "slug": "pregnancy",
      "name": "Pregnancy",
      "category_slug": "reproductive-health",
      "description": "Monitor pregnancy milestones, LMP, and estimated due date.",
      "icon": { "type": "lucide", "value": "Baby" },
      "color": "#ec4899",
      "schedule_kind": "state",
      "metadata_schema": {
        "fields": [
          { "name": "lmp",    "label": "LMP Date",         "type": "date",   "required": false },
          { "name": "edd",    "label": "EDD Date",         "type": "date",   "required": false },
          { "name": "trimester", "label": "Current Trimester", "type": "number", "required": false }
        ]
      }
    }
  ]
}
```

The seed loader is **idempotent** — re-runs upsert by `slug`. Editing a field and restarting reconciles existing rows; nothing is ever deleted by a seed.

## 3. Top-level fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `slug` | string | yes | Kebab-case stable identifier (`pregnancy`, `pain-episode`). Globally unique. Used as the i18n key suffix. |
| `name` | string | yes | Display name. The English source — localizations live in the frontend (§9). |
| `category_slug` | string | yes | Slug of the parent `event_category` concept (§8). Falls back to `general-event` if omitted; every type must belong to a category. |
| `description` | string | no | English source description. Shown in the type picker + the selected-type bar. Localizations live in the frontend (§9). |
| `icon` | object | no | `{type: "lucide", value: "<IconName>"}`. The frontend maps known slugs to specific icons in `EventTypeCard.tsx` — adding a new icon requires a `case` in that switch. |
| `color` | string | no | Hex color (`#ec4899`) used for the type's accent in cards, chips, and the selected-type bar. |
| `schedule_kind` | enum | yes | How the event renders on the calendar (§4). |
| `metadata_schema` | object | no | Dynamic form fields (§5). |
| `default_duration_days` | integer | no | Journey-template hint — typical duration for the type. Surfaced via the insights endpoint. |
| `phases` | array | no | Journey-template phases (§6). |
| `milestones` | array | no | Journey-template milestones (§6). |
| `severity_scale` | object | no | Journey-template severity config (§6). |

> **Identity model:** slug is the stable join key; `name` is mutable display. Cross-references between seed files use slugs (e.g., `category_slug` references a concept slug in `concepts.json`).

## 4. `schedule_kind` — calendar rendering

Required (NOT NULL since Phase 8a + 8e). Declares how instances of this type should render on calendar/schedule surfaces. Mirrors `backend/app/models/enums.py:ScheduleKind`.

| Value | Calendar behavior | Example types |
|---|---|---|
| `state` | One card on the onset date. **Never expanded per-day.** Active conditions surface on every day they cover via the day-cell pill + day-detail panel. Mark Resolved to record an End Date and track total duration. | Pregnancy, Pain Episode, Dental, Vision |
| `range` | One card on the onset date carrying an `endDate` (from `resolved_date`). Bounded episode by definition — End Date is always visible in the form. | Flare-up, Surgical Recovery, Aesthetic |
| `recurring` | Expands per-day based on `event_metadata.frequency` (daily / weekly / monthly). The recurrence block is always visible in the form for this kind. Mark Resolved to stop generating occurrences. | Routine Maintenance |
| `point` | Single-day incident — one card on the date, no End Date field shown. | Acute Accident |

### Defaults at the column level

- `schedule_kind` defaults to `state` (the safe "never per-day expansion" rendering) at the DB level. The seed loader also falls back to `state` if a seed entry omits the field — but every shipped seed declares it.
- `category_concept_id` is NOT NULL with `ondelete="RESTRICT"` (Phase 8e). The seed loader falls back to the system `general-event` concept if a seed entry omits `category_slug`.

## 5. `metadata_schema` — dynamic form fields

A `ClinicalEventType` can declare a typed set of extra fields that the user fills in when creating an instance. The frontend renders these dynamically via `DynamicMetadataForm`. The schema is validated by `app/schemas/clinical_event.py:MetadataSchema` (Pydantic) on create/update — a malformed seed entry raises instead of silently rendering nothing.

### Structure

```json
"metadata_schema": {
  "fields": [
    {
      "name": "lmp",
      "label": "LMP Date",
      "type": "date",
      "required": false,
      "placeholder": "YYYY-MM-DD"
    }
  ]
}
```

`fields` must contain at least one entry, and field `name`s must be unique within the schema.

### `MetadataFieldType` values

| Value | Renders as | Extra fields |
|---|---|---|
| `text` | Text input | `placeholder` |
| `number` | Number input | `placeholder`, `min`, `max` |
| `date` | `DatePicker` | — |
| `boolean` | Toggle | — |
| `catalog-select` | Catalog item picker — searches another Health Assistant catalog | `catalogs` (required), `multi`, `concept_kind`, `relation` |

### `catalog-select` deep-dive

A `catalog-select` field lets the form pick items from another catalog (anatomy, biomarkers, medications, etc.). It declares which catalogs it may search and how the picked item relates to the event.

```json
{
  "name": "affected_region",
  "label": "Affected body region",
  "type": "catalog-select",
  "catalogs": ["anatomy"],
  "multi": true,
  "relation": "primary_site"
}
```

| Field | Required | Values |
|---|---|---|
| `catalogs` | **yes** (non-empty list) | `biomarker`, `medication`, `allergy`, `anatomy`, `vaccine`, `concept` (mirrors `CatalogType` enum) |
| `multi` | no (default `false`) | Single vs. multi selection. |
| `concept_kind` | no | Only valid when `catalogs == ["concept"]`. Narrows to one ConceptKind (e.g., `event_category`, `examination_category`, `specialty`). |
| `relation` | no | How the picked item relates to the event: `primary_site`, `radiates_to`, `referred_to`, `monitors`, `treats`, `indicates` (mirrors `CatalogRelationType` enum). |

**Validation rules** (enforced by the Pydantic schema):
- A `catalog-select` field must declare a non-empty `catalogs` list.
- `concept_kind` may only be set when `catalogs` is exactly `["concept"]` — a kind filter is meaningless for other catalogs.
- Non-catalog field types (`text`, `number`, etc.) silently ignore `catalogs` / `relation` / `multi` — they're not errors, just no-ops (lets a seed carry harmless defaults without raising).

## 6. Journey templates (optional)

Three optional fields declare a "journey template" that powers the insights endpoint (`GET /clinical-events/{id}/insights`):

- **`default_duration_days`** — typical duration for the type. The insights endpoint uses this to compute "are we past the expected duration?" flags.
- **`phases`** — array of phase descriptors. Each phase has a name + a duration window relative to onset. Surfaced via the insights endpoint as `current_phase`.
- **`milestones`** — array of milestone descriptors. Each milestone has a name + an expected offset from onset. Surfaced as `upcoming_milestones` + `overdue_milestones`.
- **`severity_scale`** — configures the severity slider for episodes of this type. Numeric or categorical.

These are read by `clinical_event_service.get_event_insights`. They don't affect rendering — only analytics.

## 7. Category system — `event_category` concepts

Every `ClinicalEventType` belongs to exactly one **category** — a `Concept` of kind `event_category` (see [TAXONOMY.md](TAXONOMY.md)). Categories are seeded in `backend/data/seeds/concepts.json`:

```json
{
  "slug": "reproductive-health",
  "name": "Reproductive Health",
  "color": "#ec4899",
  "icon": { "type": "lucide", "value": "baby" },
  "kinds": ["event_category"]
}
```

### Shipped categories

| Slug | Name | Types |
|---|---|---|
| `reproductive-health` | Reproductive Health | Pregnancy |
| `acute-chronic` | Acute & Chronic | Pain Episode, Chronic Flare-up, Acute Accident |
| `specialized-care` | Specialized Care | Surgical Recovery, Dental Journey, Vision & Ophthalmology, Aesthetic & Skin |
| `routine-wellness` | Routine & Wellness | Routine Maintenance |
| `general-event` | General | *(system fallback — types that don't fit a more specific specialty)* |

### Adding a new category

1. Add a concept entry to `backend/data/seeds/concepts.json` with `"kinds": ["event_category"]` and a unique `slug`.
2. Add the slug to the `case` block in `frontend/src/components/events/EventTypeCard.tsx:getEventIcon` so the picker shows an icon for it.
3. Add frontend translations under `events.category.{slug}.{name,description}` in both `frontend/src/locales/en/common.json` and `frontend/src/locales/el/common.json` (§9).
4. Restart — the seed loader reconciles the new concept, and types can reference it via `category_slug`.

The `general-event` category is **always present** (seeded by `concepts.json` + defensively re-inserted by migration `p8e5f6g7h8i9` on fresh DBs). It's the NOT-NULL backfill target for types whose seed entry omits `category_slug`. You should rarely need to assign a type to it — every shipped type fits one of the four domain categories above.

## 8. Localization (i18n)

The seed stores **English source strings** (`name`, `description` for both types and categories). The frontend translates them at display time via two slug-keyed i18n namespaces in `frontend/src/locales/{en,el}/common.json`:

```
events.type.{slug}.name           → "Pregnancy" / "Εγκυμοσύνη"
events.type.{slug}.description    → "Monitor pregnancy milestones..." / "Παρακολούθηση οροσημείων εγκυμοσύνης..."
events.category.{slug}.name       → "Reproductive Health" / "Αναπαραγωγική Υγεία"
events.category.{slug}.description → "Pregnancy, fertility, and..." / "Εγκυμοσύνη, γονιμότητα και..."
```

**Fallback behavior:** every display site calls `t(`events.type.${slug}.name`, type.name)` — the second argument is the fallback. A custom tenant-created type whose slug isn't in the i18n files renders its raw backend `name`/`description` instead of breaking. Localization is **progressive** — you don't have to ship translations for every custom type.

**Bilingual search:** the type-picker search (`buildSearchableIndex` in `frontend/src/utils/clinicalEventSearch.ts`) matches **both** the English source strings and the current locale's translations. A Greek user can find "Pregnancy" by typing either `pregnancy` or `εγκυμοσύνη`.

## 9. Occurrences (point-in-time episodes)

In addition to the type-driven form fields, certain types (Pain Episode, Flare-up) support discrete **occurrences** — point-in-time episodes nested under the parent event. Each occurrence carries:

- `date` + `time` — when it happened
- `intensity` — 1–10 scale (rendered via `ScaleSlider`)
- `location` — anatomy link (rendered via `CatalogField` with `allowedTypes={['anatomy']}`)
- `notes` — free text

Occurrences emit as `kind='point'` events on their dates (in addition to the parent's `state`/`range` rendering). They live in the dedicated `clinical_event_occurrences` table (`ClinicalEventOccurrence` at `backend/app/models/clinical_event.py`); `ClinicalEvent._serialize_occurrences()` reads from that table first and only falls back to the legacy `occurrences` JSONB column on the event row when the relationship isn't loaded. The form gates the occurrence-tracking section by type slug (`pain-episode` / `flare-up`).

## 10. Worked examples — the 9 shipped types

| Slug | Category | Kind | Notable metadata fields |
|---|---|---|---|
| `pregnancy` | reproductive-health | state | LMP Date, EDD Date, Current Trimester |
| `pain-episode` | acute-chronic | state | *(supports episode tracking)* |
| `flare-up` | acute-chronic | range | *(supports episode tracking)* |
| `accident` | acute-chronic | point | *(single-day incident)* |
| `surgical-recovery` | specialized-care | range | |
| `dental` | specialized-care | state | |
| `vision` | specialized-care | state | |
| `aesthetic` | specialized-care | range | |
| `maintenance` | routine-wellness | recurring | *(recurrence block: weekly/monthly)* |

To see the full JSON for any of these, look at `backend/data/seeds/clinical_event_types.json`.

## 11. Adding a new type — checklist

1. **Pick a slug** — kebab-case, unique across all types.
2. **Pick a category** — one of the shipped `event_category` slugs (§7), or add a new category first.
3. **Choose `schedule_kind`** based on the desired calendar behavior (§4).
4. **Declare `metadata_schema`** if the type needs custom form fields (§5). Otherwise omit.
5. **Add the type entry** to `backend/data/seeds/clinical_event_types.json`.
6. **Add the slug to `getEventIcon`** in `frontend/src/components/events/EventTypeCard.tsx` so the picker shows an icon.
7. **Add translations** under `events.type.{slug}.{name,description}` in both `en/common.json` and `el/common.json` (§8). Optional but recommended.
8. **Restart** — `SeedService.seed_clinical_event_types` reconciles the new row.

## 12. Endpoints (summary)

The full REST contract is in [API.md](API.md). The key endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/clinical-events/types` | List all event types (used by the form's picker). |
| `POST` | `/api/v1/clinical-events/types` | Create a type (admin). Body validates against `ClinicalEventTypeBase` — `schedule_kind` and `category_concept_id` are required. |
| `GET` | `/api/v1/clinical-events` | List events. Supports `patient_id`, `examination_id`, `active_on`, `onset_on`, `date_range` filters (Phase 2). |
| `POST` | `/api/v1/clinical-events` | Create an event instance. |
| `GET` | `/api/v1/clinical-events/{id}` | Read one event (with type_details, examinations, observations, anatomy_links). |
| `POST` | `/api/v1/clinical-events/{id}/occurrences` | Add a discrete occurrence (§9). |
| `POST` | `/api/v1/clinical-events/{id}/link-anatomy` | Link an anatomy structure with a relation type. |
| `GET` | `/api/v1/clinical-events/{id}/insights` | Journey-template analytics (§6). |

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — where clinical events fit in the overall data model.
- [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md) — the seed pipeline (clinical_event_types is stage 5).
- [TAXONOMY.md](TAXONOMY.md) — the concept + edge system that categories are part of.
- [API.md](API.md) — the full REST API reference.
- [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md) — clinical events are exposed as FHIR `Condition` resources.

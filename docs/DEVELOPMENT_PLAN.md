# Health Assistant — Development Roadmap

The roadmap for Health Assistant — upcoming features for the self-hosted, open-source health records platform. (For what's already shipped, see [STATUS.md](STATUS.md).)

## Shipped: Biomarker Clinical Insights & Correlations

*Originally an upcoming item; shipped in 0.3.x. Kept here for design context — the live implementation is documented in [TAXONOMY.md](TAXONOMY.md) and [API.md](API.md).*

### Goal
Enhance the biomarker system to provide deeper clinical insights, better categorization, and correlations with patient symptoms (e.g., organ pain).

### What shipped

- **`BiomarkerDefinition` clinical context fields**: clinical scope / high-implication / low-implication text fields are present on the model and surfaced through the biomarker schemas + BiomarkerDetail UI (stratified reference-range table + clinical-context sections).
- **Biomarker correlation storage** migrated to the unified **`concept_edges`** table (the legacy `BiomarkerCorrelation` table was dropped during the Phase 3 consolidation — see `alembic/versions/8ddb7ef7ca4d_consolidated_baseline.py`). Two relation kinds cover the original intent:
  - biomarker ↔ anatomy (`AFFECTS` edge — e.g. creatinine → left-kidney),
  - biomarker ↔ clinical_event_type (`MONITORS` edge — e.g. fasting glucose → endocrine-system).
  The CRUD endpoints `POST/GET/DELETE /api/v1/clinical-events/types/{type_id}/biomarkers` write these edges directly. The `ClinicalEventEngine` reads them to drive the recommended-biomarker insight on event detail pages.

### Genuinely remaining work

- **`/api/v1/biomarkers/correlated` query endpoint**: the reverse lookup ("which biomarkers affect this organ?") is reachable today only via the catalog graph traversal endpoint (`GET /catalogs/{type}/{id}/relations`). A purpose-built biomarker-centric query endpoint (`backend/app/api/v1/endpoints/biomarkers.py:43` carries the TODO) is not yet wired.
- **AI backfill script** for missing `clinical_scope` / `high_implication` / `low_implication` text on legacy definitions — `scripts/backfill_biomarker_insights.py` is not yet written.

---

## Shipped: Biomarker–Clinical Event Binding & Ophthalmic Support

*Originally an upcoming item; shipped in 0.3.x. The two database models listed below are live, and the ophthalmology seed is partially landed.*

### Goal
Extend the system to support specialized medical domains like Ophthalmology by linking chronic conditions (Clinical Events) with quantitative measurements (Biomarkers).

### What shipped

- **`event_observation_links` table** (`backend/app/models/clinical_event.py:132`, ORM class `EventObservationLink`): maps a specific `ClinicalEvent` instance to individual `Observation` records. Used by `clinical_event_service.py` to power the "linked labs" tab on event detail pages and the biomarker-insight aggregation.
- **Binding endpoints** on the clinical-events router: `POST /{event_id}/link-observation`, `GET /{event_id}/insights`. The type-level binding (`POST/GET/DELETE /types/{type_id}/biomarkers`) is the edge-based path described above.
- **Ophthalmology seed (partial)**: the `ophthalmology` body-system concept exists in `data/seeds/concepts.json`; the `diopters` unit + a diopters-carrying field on the ophthalmology clinical-event-type are in `data/seeds/clinical_event_types.json`.

### Genuinely remaining work

- **Full ophthalmic biomarker set**: Visual Acuity (OD/OS), Intraocular Pressure (OD/OS), Refraction Sphere/Cylinder/Axis biomarkers are not yet in `data/seeds/default_catalog.json` — only the surrounding scaffolding (concept + unit + event type) is seeded.
- **Ophthalmic event types**: Refractive Error (Myopia, Presbyopia), Cataract, Glaucoma Suspect event types beyond the one ophthalmology type currently shipped.
- **AI extraction**: the AI extraction schemas do not yet emit the new ocular biomarkers when present in uploaded examination documents.

---

## See also

- [STATUS.md](STATUS.md) — current implementation state + Beta roadmap.
- [TAXONOMY.md](TAXONOMY.md) — `concept_edges` relation semantics (`AFFECTS`, `MONITORS`).
- [CLINICAL_EVENTS.md](CLINICAL_EVENTS.md) — clinical-event type blueprint + the biomarker-binding endpoints.
- [API.md](API.md) — REST reference for `/clinical-events/types/{id}/biomarkers` and `/clinical-events/{id}/insights`.

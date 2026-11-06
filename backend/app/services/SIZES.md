# Service-layer sizes — health-assistant/core

Ledger for the family service-cohesion law (band edge ~780, trigger ~800).
One row per file that crossed the band at the 2026-10-25 baseline, in the
plan-40 W0 format (`| path | lines (dated) | accept | decline (dated, reason) |`).
Paths are as measured at the flat `services/` baseline; the W2 stages moved
the giants into packages (row resolution notes where each landed). Lines are
the 2026-10-25 measurements that seeded this ledger.

| path (as measured 2026-10-25) | lines (dated) | accept | decline (dated, reason) |
|---|---|---|---|
| `import_service.py` | 3,029 (2026-10-25) | split 2026-10-25 → `services/imports/` (ten section modules + types + composition root, largest 634; 51 methods AST-verbatim; commit b3e9a1a0) | — |
| `analytics_service.py` | 1,545 (2026-10-25) | split 2026-10-25 → `services/analytics/` (status · dashboard · trends 761 · insights · state_history + types; 9 functions AST-verbatim; commit 9bdadd8e) | — |
| `integration_sync_service.py` | 1,509 (2026-10-25) | extracted 2026-10-25 → resident orchestrator (562) + `integration/sync_legs.py` (614) + `sync_notifications.py` (427) + `sync_support.py` (58); 7 emitters + 10 legs AST/statement-verbatim; commit 06ef44ae | — |
| `seed_service.py` | 1,286 (2026-10-25) | split 2026-10-25 → `services/seed/` entity-family modules behind the composed `SeedService` (largest 299; 26 methods AST-verbatim; commit 212f5688) | — |
| `fhir_service.py` | 1,231 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): facade + converter + helpers already separated (fhir_converter, fhir_helpers, fhir_extensions, fhir_facade_service, provenance_service sit beside it in `services/fhir/`); the residual is one cohesive facade surface. Re-propose on touch or +20% growth |
| `clinical_event_service.py` | 1,044 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): engine already separated (`clinical_event_engine.py`); the residual is CRUD + typed access checks over one aggregate. Carries 12 function-level `fastapi.HTTPException` imports — a dated layering debt for a future typed-exception round, not this one. Re-propose on touch or +20% |
| `erasure.py` | 1,040 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): single concern (the erasure walker + its ledger) in `services/data_lifecycle/`; splitting the walker from its ledger would couple two files to one transaction. Re-propose on touch or +20% |
| `export_service.py` | 1,020 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): one export pipeline (backup bundles + streaming exports) whose halves share the manifest/job model (`export_import_job`); no seam without a behavior-risking split. Re-propose on touch or +20% |
| `notification_service.py` | 958 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): already decomposed satellites (notification_manager, notification_rule_service, notification_kind_registry, notification_preferences_service, notification_targets, mobile_push, webpush all in `services/notifications/`); the residual is the emit core. Re-propose on touch or +20% |
| `setup_checklist_service.py` | 895 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): evaluator-registry shape (pluggable steps + extension-catalog builder); one concern, registry-driven. Re-propose on touch or +20% |
| `catalog_search_service.py` | 865 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): hybrid search pipeline (trigram + FTS + RRF) is one algorithm, not fused concerns. Re-propose on touch or +20% |
| `document_service.py` | 799 (2026-10-25) | — | declined 2026-10-25 (plan 40 W2): at-band, single ingestion concern (`services/documents/`). Re-propose on touch or +20% |

W2 resolution (all commits 2026-10-25): the four giants are packages; the eight
non-giant over-band files carry dated declines above. The layering debt (four
services importing `fastapi.HTTPException` — audit_service, clinical_event_service,
document_service, tenant_admin_service) is recorded, not fixed here (zero-behavior
round). Stale prose kept byte-stable by the OpenAPI-empty proof: two endpoint
docstrings + one model enum docstring still name the flat proposal paths, and
alembic 0007's docstring names `app.services.retention_policies` — all dated
here for a future description-only docs pass.

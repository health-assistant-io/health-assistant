"""Per-source sync legs for the integration sync pipeline.

Moved verbatim out of run_sync (plan 40 W2d): one function per opt-in
data class, same call order and shared-mutation semantics (attempts and
ingest_report are the caller's objects; counters come back as returns)."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions.registry import integration_registry
from app.services.integration import integration_ingest
from app.services.integration.sync_notifications import _emit_hitl_proposal_notification
from app.services.integration.sync_support import (
    INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC,
    INTEGRATION_MAX_PROPOSALS_PER_SYNC,
    _declared,
)

logger = logging.getLogger(__name__)


async def _leg_pull_and_ingest(
    db: AsyncSession, integration: Any, provider: Any, ingest_report: Any
) -> Any:
    from app.services.integration.integration_ingest import ingest_observations

    # ---- pull ----
    # The ``sync`` capability gates the pull leg: extensions without it
    # (webhook, bridge, mcp_client) have no beat-driven ingestion — their
    # ``pull_data`` default is a no-op, so skipping the call is
    # behavior-preserving and honest (declared capabilities, not probes).
    if integration_registry.has_capability(integration.provider, "sync"):
        observations_data = await provider.pull_data(integration)
    else:
        observations_data = []
    pulled = len(observations_data) if observations_data else 0

    # The one inbound funnel owns validation, biomarker mapping, dedup,
    # the FHIR/telemetry split, and mapping-gap recording (plan 8 W2).
    attempts: list[str] = []
    telemetry_count = 0
    fhir_count = 0
    dropped_invalid = 0
    if observations_data:
        obs_report = await ingest_observations(
            db,
            integration,
            observations_data,
            provider_name=integration.provider,
            instance_name=integration.instance_name,
        )
        ingest_report.merge(obs_report)
        attempts.append("observations")
        telemetry_count = obs_report.written_by_stream.get("telemetry", 0)
        fhir_count = obs_report.written_by_stream.get("observations", 0)
        dropped_invalid = obs_report.rejected
    else:
        attempts.append("observations")

    return observations_data, pulled, telemetry_count, fhir_count, dropped_invalid, attempts


async def _leg_appointments(
    db: AsyncSession, integration: Any, provider: Any, ingest_report: Any, attempts: list[str]
) -> Any:
    from app.services.integration.integration_ingest import ingest_appointments

    # ---- appointments (clinical events + examinations, opt-in) ----
    # Providers that declare the ``clinical_event`` / ``examination``
    # data classes pull longitudinal events (conditions, admissions)
    # and Encounters. The funnel resolves a service-context
    # actor from the owner and writes through the canonical services,
    # deduping on ``(tenant, patient, source_integration_id,
    # external_id)`` when the provider sets ``external_id``. Per-item
    # failures are isolated and reported, never aborting the sync.
    events_pulled = 0
    events_written = 0
    exams_pulled = 0
    exams_written = 0
    # Hoisted map so the documents block (workstream C) can resolve a
    # ``DocumentPull.examination_external_id`` against the exam that was
    # just pulled+persisted above. Keyed by the upstream external_id
    # the provider set on ``ExaminationCreate``; value is the resulting
    # exam's UUID.
    exam_by_external_id: dict[str, UUID] = {}
    declares_events = _declared(provider, "clinical_event")
    declares_exams = _declared(provider, "examination")
    if declares_events or declares_exams:
        events_data = []
        exams_data = []
        try:
            if declares_events:
                events_data = (await provider.pull_clinical_events(integration)) or []
            if declares_exams:
                exams_data = (await provider.pull_examinations(integration)) or []
        except Exception as pull_err:
            logger.warning(
                "appointments pull failed for %s: %s",
                integration.provider,
                pull_err,
            )
        events_pulled = len(events_data)
        exams_pulled = len(exams_data)
        if events_data or exams_data:
            from app.services.integration.integration_actor import resolve_integration_actor

            actor = await resolve_integration_actor(db, integration)
            appt_report, exam_by_external_id = await ingest_appointments(
                db,
                integration,
                examinations=exams_data,
                clinical_events=events_data,
                actor=actor,
            )
            ingest_report.merge(appt_report)
            attempts.append("appointments")
            exams_written = appt_report.written_by_kind.get("examinations", 0)
            events_written = appt_report.written_by_kind.get("clinical_events", 0)
            if appt_report.deduped or appt_report.rejected:
                logger.info(
                    "appointments sync: provider %s pulled %d event(s)/%d "
                    "exam(s), wrote %d (deduped=%d rejected=%d)",
                    integration.provider,
                    events_pulled,
                    exams_pulled,
                    appt_report.written,
                    appt_report.deduped,
                    appt_report.rejected,
                )

    return events_pulled, events_written, exams_pulled, exams_written, exam_by_external_id


async def _leg_catalog_proposals(db: AsyncSession, integration: Any, provider: Any) -> Any:
    # ---- catalog proposals (opt-in hook, workstream F) ----
    # Providers that declare the ``catalog_proposal`` data class can
    # contribute catalog entries (biomarker classes, medications,
    # concepts, typed concept edges) sourced from upstream — closing
    # the gap left by ``ConceptProvenance.INTEGRATION`` (declared in
    # the enum but unwritten before F). Each returned proposal is
    # applied through ``catalog_proposal_service.apply_proposal``,
    # which routes by ``kind`` to the matching service-layer write
    # path and is idempotent on the natural key per kind — re-syncs
    # don't duplicate. Capped at ``INTEGRATION_MAX_PROPOSALS_PER_SYNC``
    # items; excess dropped with a warning. Failures are logged and
    # don't abort the sync (mirrors the events / exams hooks).
    proposals_pulled = 0
    proposals_applied = 0
    last_apply_result = None
    proposals_ran = False
    declares_proposals = _declared(provider, "catalog_proposal")
    if declares_proposals:
        try:
            proposals_data = await provider.pull_catalog_proposals(integration)
            proposals_data = proposals_data or []
            proposals_pulled = len(proposals_data)
            if proposals_data:
                from app.services.catalog.catalog_proposal_service import (
                    apply_proposal,
                )
                from app.services.integration.integration_actor import (
                    resolve_integration_actor,
                )

                actor = await resolve_integration_actor(db, integration)
                dropped_by_cap = 0
                proposals_ran = True
                for idx, proposal in enumerate(proposals_data):
                    if idx >= INTEGRATION_MAX_PROPOSALS_PER_SYNC:
                        dropped_by_cap = proposals_pulled - idx
                        logger.warning(
                            "catalog-proposals sync: provider %s returned "
                            "%d proposals — cap is %d; dropping the last "
                            "%d.",
                            integration.provider,
                            proposals_pulled,
                            INTEGRATION_MAX_PROPOSALS_PER_SYNC,
                            dropped_by_cap,
                        )
                        break
                    try:
                        last_apply_result = await apply_proposal(db, actor, integration, proposal)
                        if last_apply_result.created:
                            proposals_applied += 1
                    except Exception as proposal_err:
                        logger.warning(
                            "apply_proposal failed for integration %s proposal #%d (kind=%s): %s",
                            integration.id,
                            idx,
                            getattr(proposal, "kind", "?"),
                            proposal_err,
                        )
        except Exception as proposals_pull_err:
            logger.warning(
                "pull_catalog_proposals failed for %s: %s",
                integration.provider,
                proposals_pull_err,
            )
        else:
            if proposals_pulled and proposals_applied < proposals_pulled:
                logger.info(
                    "catalog-proposals sync: provider %s pulled %d, "
                    "applied %d new (%d already existed or failed)",
                    integration.provider,
                    proposals_pulled,
                    proposals_applied,
                    proposals_pulled - proposals_applied,
                )

    return proposals_pulled, proposals_applied, last_apply_result, proposals_ran


async def _leg_hitl_proposals(db: AsyncSession, integration: Any, provider: Any) -> Any:
    # ---- HITL proposals (opt-in hook, workstream G) ----
    # The HITL layer is the human-in-the-loop counterpart to the
    # catalog-proposals block above. Same payload shapes, but the
    # integration asks the platform to queue each proposal for human
    # review instead of auto-applying. The engine persists each spec
    # as a PROPOSED row in the one ``proposals`` table
    # (source='extension', source_ref = this instance) + fires an HITL
    # notification. Re-emitting the same spec on the next sync is a
    # no-op (deduped via ``create_proposal``'s tenant-scoped dedup_key
    # check). The user resolves via the
    # ``/api/v1/integrations/instance/{id}/proposals/.../resolve``
    # endpoint, which validates the (possibly-edited) payload against the
    # kind contract and runs the kind's resolver (catalog service or the
    # canonical clinical service). Capped at
    # ``INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC`` items; excess
    # dropped with a warning. Failures are logged and don't abort
    # the sync (mirrors the events / exams / catalog-proposals hooks).
    hitl_pulled = 0
    hitl_inserted = 0
    declares_hitl = _declared(provider, "hitl_proposal")
    if declares_hitl:
        try:
            hitl_specs = await provider.pull_hitl_proposals(integration)
            hitl_specs = hitl_specs or []
            hitl_pulled = len(hitl_specs)
            if hitl_specs:
                from app.services.integration.integration_proposal_service import (
                    create_proposal as _create_hitl_proposal,
                )

                hitl_dropped_by_cap = 0
                for idx, spec in enumerate(hitl_specs):
                    if idx >= INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC:
                        hitl_dropped_by_cap = hitl_pulled - idx
                        logger.warning(
                            "hitl-proposals sync: provider %s returned "
                            "%d proposals — cap is %d; dropping the "
                            "last %d.",
                            integration.provider,
                            hitl_pulled,
                            INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC,
                            hitl_dropped_by_cap,
                        )
                        break
                    try:
                        proposal_type = getattr(spec, "proposal_type", None)
                        if not proposal_type:
                            logger.warning(
                                "hitl-proposals sync: provider %s spec "
                                "#%d missing proposal_type — skipping",
                                integration.provider,
                                idx,
                            )
                            continue
                        _, created = await _create_hitl_proposal(
                            db,
                            integration_id=integration.id,
                            tenant_id=integration.tenant_id,
                            patient_id=getattr(spec, "patient_id", None),
                            proposal_type=proposal_type,
                            title=getattr(spec, "title", "(untitled)"),
                            proposed_payload=getattr(spec, "proposed_payload", {}),
                            context=getattr(spec, "context", {}) or {},
                            created_by=integration.user_id,
                        )
                        if created:
                            hitl_inserted += 1
                            # Fire the HITL notification only for newly-
                            # inserted rows so re-syncs don't spam the
                            # inbox. Best-effort; failures logged.
                            await _emit_hitl_proposal_notification(integration, spec)
                    except Exception as spec_err:
                        logger.warning(
                            "create_proposal failed for integration %s HITL spec #%d (type=%s): %s",
                            integration.id,
                            idx,
                            getattr(spec, "proposal_type", "?"),
                            spec_err,
                        )
        except Exception as hitl_pull_err:
            logger.warning(
                "pull_hitl_proposals failed for %s: %s",
                integration.provider,
                hitl_pull_err,
            )
        else:
            if hitl_pulled and hitl_inserted < hitl_pulled:
                logger.info(
                    "hitl-proposals sync: provider %s pulled %d, "
                    "inserted %d new (%d already existed or failed)",
                    integration.provider,
                    hitl_pulled,
                    hitl_inserted,
                    hitl_pulled - hitl_inserted,
                )

    return hitl_pulled, hitl_inserted


async def _leg_documents(
    db: AsyncSession,
    integration: Any,
    provider: Any,
    ingest_report: Any,
    attempts: list[str],
    exam_by_external_id: dict[str, UUID],
) -> Any:
    from app.services.integration.integration_ingest import ingest_documents

    # ---- documents (opt-in hook, workstream C) ----
    # Providers that declare the ``document`` data class deliver
    # pre-fetched document bytes; the funnel routes each one through
    # ``document_service.ingest_document_bytes`` — the same canonical path
    # the UI upload uses, so OCR/extraction rides the AI gateway (S1 rule:
    # no direct model construction anywhere). The funnel owns the per-sync
    # caps (20 docs / 50 MiB) and the ``(integration, external_id)`` dedup
    # check, so a re-sync re-files nothing and re-runs no OCR.
    docs_pulled = 0
    docs_written = 0
    declares_docs = _declared(provider, "document")
    if declares_docs:
        attempts.append("documents")
        try:
            docs_data = await provider.pull_documents(integration)
            docs_data = docs_data or []
            docs_pulled = len(docs_data)
            if docs_data:
                from app.services.integration.integration_actor import (
                    resolve_integration_actor,
                )

                actor = await resolve_integration_actor(db, integration)
                docs_report = await ingest_documents(
                    db,
                    integration,
                    docs_data,
                    actor=actor,
                    examination_by_external_id=exam_by_external_id,
                )
                ingest_report.merge(docs_report)
                docs_written = docs_report.written
                if docs_report.deduped or docs_report.rejected:
                    logger.info(
                        "documents sync: provider %s pulled %d, wrote %d (deduped=%d rejected=%d)",
                        integration.provider,
                        docs_pulled,
                        docs_written,
                        docs_report.deduped,
                        docs_report.rejected,
                    )
        except Exception as docs_pull_err:
            logger.warning(
                "pull_documents failed for %s: %s",
                integration.provider,
                docs_pull_err,
            )

    return docs_pulled, docs_written


async def _leg_medications(db: AsyncSession, integration: Any, provider: Any) -> Any:
    # ---- medications (opt-in hook, Phase 4 Route A) ----
    # Providers that declare the ``medication`` data class can pull
    # patient medications (FHIR MedicationStatement / MedicationRequest). The
    # engine resolves the actor (re-uses the one from the prior step
    # if that ran) and writes each record via
    # ``medication_service.add_patient_medication`` (refactored to the
    # canonical TokenData + dedup-kwargs shape). Dedup on
    # (tenant, patient, source_integration_id, external_id) when the
    # provider sets ``external_id``. Failures are logged and don't
    # abort the sync (mirrors the events / exams / docs hooks).
    meds_pulled = 0
    meds_written = 0
    declares_meds = _declared(provider, "medication")
    if declares_meds:
        try:
            meds_data = await provider.pull_medications(integration)
            meds_data = meds_data or []
            meds_pulled = len(meds_data)
            if meds_data:
                from app.services.clinical.medication_service import (
                    add_patient_medication,
                )
                from app.services.integration.integration_actor import (
                    resolve_integration_actor,
                )

                actor = await resolve_integration_actor(db, integration)
                for med_payload in meds_data:
                    try:
                        await add_patient_medication(
                            db,
                            actor,
                            med_payload,
                            source_integration_id=integration.id,
                            external_id=getattr(med_payload, "external_id", None),
                        )
                        meds_written += 1
                    except Exception as med_err:
                        logger.warning(
                            "add_patient_medication failed for integration %s med %r: %s",
                            integration.id,
                            getattr(med_payload, "external_id", None),
                            med_err,
                        )
        except Exception as meds_pull_err:
            logger.warning(
                "pull_medications failed for %s: %s",
                integration.provider,
                meds_pull_err,
            )

    return meds_pulled, meds_written


async def _leg_allergies(db: AsyncSession, integration: Any, provider: Any) -> Any:
    # ---- allergies (opt-in hook, Phase 4 Route A) ----
    # Same shape as medications. Providers that declare the ``allergy``
    # data class pull FHIR AllergyIntolerance resources;
    # the engine writes each via ``allergy_service.add_patient_allergy``.
    allergies_pulled = 0
    allergies_written = 0
    declares_allergies = _declared(provider, "allergy")
    if declares_allergies:
        try:
            allergy_data = await provider.pull_allergies(integration)
            allergy_data = allergy_data or []
            allergies_pulled = len(allergy_data)
            if allergy_data:
                from app.services.clinical.allergy_service import (
                    add_patient_allergy,
                )
                from app.services.integration.integration_actor import (
                    resolve_integration_actor,
                )

                actor = await resolve_integration_actor(db, integration)
                for allergy_payload in allergy_data:
                    try:
                        await add_patient_allergy(
                            db,
                            actor,
                            allergy_payload,
                            source_integration_id=integration.id,
                            external_id=getattr(allergy_payload, "external_id", None),
                        )
                        allergies_written += 1
                    except Exception as allergy_err:
                        logger.warning(
                            "add_patient_allergy failed for integration %s allergy %r: %s",
                            integration.id,
                            getattr(allergy_payload, "external_id", None),
                            allergy_err,
                        )
        except Exception as allergies_pull_err:
            logger.warning(
                "pull_allergies failed for %s: %s",
                integration.provider,
                allergies_pull_err,
            )

    return allergies_pulled, allergies_written


async def _leg_immunizations(db: AsyncSession, integration: Any, provider: Any) -> Any:
    # ---- immunizations (opt-in hook, Phase 4 Route A) ----
    # Same shape. Providers that declare the ``immunization`` data class
    # pull FHIR Immunization resources; the engine writes each via
    # ``vaccine_service.add_patient_immunization``.
    immuns_pulled = 0
    immuns_written = 0
    declares_immuns = _declared(provider, "immunization")
    if declares_immuns:
        try:
            immun_data = await provider.pull_immunizations(integration)
            immun_data = immun_data or []
            immuns_pulled = len(immun_data)
            if immun_data:
                from app.services.clinical.vaccine_service import (
                    add_patient_immunization,
                )
                from app.services.integration.integration_actor import (
                    resolve_integration_actor,
                )

                actor = await resolve_integration_actor(db, integration)
                for immun_payload in immun_data:
                    try:
                        await add_patient_immunization(
                            db,
                            actor,
                            immun_payload,
                            source_integration_id=integration.id,
                            external_id=getattr(immun_payload, "external_id", None),
                        )
                        immuns_written += 1
                    except Exception as immun_err:
                        logger.warning(
                            "add_patient_immunization failed for "
                            "integration %s immunization %r: %s",
                            integration.id,
                            getattr(immun_payload, "external_id", None),
                            immun_err,
                        )
        except Exception as immuns_pull_err:
            logger.warning(
                "pull_immunizations failed for %s: %s",
                integration.provider,
                immuns_pull_err,
            )

    return immuns_pulled, immuns_written


async def _leg_labs(
    db: AsyncSession, integration: Any, provider: Any, ingest_report: Any, attempts: list[str]
) -> Any:
    # ---- labs (opt-in hook, plan 8 W4) ----
    # Providers that declare the ``lab_report`` data class pull lab
    # reports (FHIR DiagnosticReport payload dicts); the funnel writes
    # each through ``integration_ingest.ingest_labs`` — the one labs
    # path in — which dedups on the natural key (tenant, patient, code,
    # effective) and records per-item rejections. Typed errors from the
    # pull propagate (the observations-leg contract): a fail-closed
    # credential or an unreachable upstream fails the sync instead of
    # quietly producing zero labs forever.
    labs_pulled = 0
    labs_written = 0
    declares_labs = _declared(provider, "lab_report")
    if declares_labs:
        attempts.append("labs")
        labs_data = await provider.pull_labs(integration)
        labs_data = labs_data or []
        labs_pulled = len(labs_data)
        if labs_data:
            from app.services.integration.integration_actor import resolve_integration_actor

            actor = await resolve_integration_actor(db, integration)
            labs_report = await integration_ingest.ingest_labs(
                db, integration, labs_data, actor=actor
            )
            ingest_report.merge(labs_report)
            labs_written = labs_report.written
            if labs_report.deduped or labs_report.rejected:
                logger.info(
                    "labs sync: provider %s pulled %d, wrote %d (deduped=%d rejected=%d)",
                    integration.provider,
                    labs_pulled,
                    labs_written,
                    labs_report.deduped,
                    labs_report.rejected,
                )

    return labs_pulled, labs_written


async def _leg_push(provider: Any, integration: Any, source: str) -> Any:
    # ---- push ----
    # Gated behind the declared ``push`` capability (same declared-data
    # rule as the eight ``pull_*`` data classes). The default detects a
    # real ``push_data`` override, so ``dev_dummy`` and ``fhir_server``
    # are still called; push-only-default providers skip the no-op.
    push_result: dict[str, Any] | None = None
    try:
        if _declared(provider, "push"):
            push_result = await provider.push_data(integration, {"status": f"{source}_sync"})
    except Exception as push_err:
        logger.warning("Push failed for %s: %s", integration.provider, push_err)
        push_result = None

    return push_result

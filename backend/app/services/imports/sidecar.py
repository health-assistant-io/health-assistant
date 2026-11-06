"""Non-FHIR sidecar restore (telemetry, triggers, exams, events, catalogs)."""

import logging

from app.services.imports.types import (
    UTC,
    UUID,
    Any,
    ClinicalEvent,
    ClinicalEventType,
    ConceptKind,
    ExaminationModel,
    NotificationTrigger,
    TelemetryDataModel,
    UserIntegration,
    _parse_date,
    _parse_dt,
    _uuid,
    datetime,
    or_,
    sa_update,
    select,
    settings,
    uuid4,
)

logger = logging.getLogger(__name__)


class SidecarMixin:
    # ---------------- non-FHIR sidecar restore ----------------

    async def restore_sidecar(
        self, name: str, payload: Any, tenant_id: UUID, id_remap: dict[str, str]
    ) -> tuple[dict[str, int], list[str], list[str]]:
        created: dict[str, int] = {}
        errors: list[str] = []
        warnings: list[str] = []

        if name == "telemetry.json":
            created["telemetry"] = await self._restore_telemetry(payload, tenant_id)
        elif name == "integrations.json":
            created["integrations"], w = await self._restore_integrations(
                payload, tenant_id, id_remap
            )
            warnings.extend(w)
        elif name == "notification_triggers.json":
            created["notification_triggers"] = await self._restore_triggers(
                payload, tenant_id, id_remap
            )
        elif name == "examinations.json":
            created["examinations"] = await self._restore_examinations(payload, tenant_id, id_remap)
        elif name == "clinical_events.json":
            created["clinical_events"] = await self._restore_clinical_events(
                payload, tenant_id, id_remap
            )
        elif name == "biomarker_definitions.json":
            created["biomarker_definitions"] = await self._restore_biomarker_catalog(
                payload, tenant_id
            )
        elif name == "medication_catalog.json":
            created["medication_catalog"] = await self._restore_medication_catalog(
                payload, tenant_id
            )
        elif name == "allergy_catalog.json":
            created["allergy_catalog"] = await self._restore_allergy_catalog(payload, tenant_id)
        elif name == "clinical_event_types.json":
            created["clinical_event_types"] = await self._restore_clinical_event_types(
                payload, tenant_id
            )
        elif name == "concepts.json":
            created["concepts"] = await self._restore_concepts(payload, tenant_id, id_remap)
        elif name == "anatomy.json":
            (
                created["anatomy_structures"],
                created["anatomy_relations"],
            ) = await self._restore_anatomy(payload, tenant_id, id_remap)
        elif name == "concept_edges.json":
            created["concept_edges"] = await self._restore_concept_edges(
                payload, tenant_id, id_remap
            )
        elif name == "ai_config.json":
            warnings.append("AI config restore is not supported in v1 (export-only).")
        elif name == "documents.json":
            pass
        else:
            warnings.append(f"Unknown sidecar {name}; skipped")
        return created, errors, warnings

    async def _restore_telemetry(self, payload: list[dict[str, Any]], tenant_id: UUID) -> int:
        """Restore long-format telemetry rows from an export sidecar.

        Each item must carry ``slug`` + ``value`` (the long-format contract);
        ``unit`` / ``patient_id`` are optional. Rows are re-canonicalized
        against the (restored) biomarker definition, and the exported device
        reference becomes a local ``telemetry_devices`` row. Items missing the
        required fields, naming an unknown slug, or lacking patient attribution
        are skipped with a warning.
        """
        from app.services.telemetry.telemetry_ingest import (
            UnknownTelemetrySlugError,
            build_telemetry_row,
            resolve_telemetry_definition,
            resolve_telemetry_device,
        )

        count = 0
        device_cache: dict[tuple[UUID, str], UUID] = {}
        for item in payload:
            try:
                slug = item.get("slug")
                value = item.get("value")
                if slug is None or value is None:
                    logger.warning(
                        "telemetry row skipped: missing slug or value (%r)",
                        item,
                    )
                    continue
                patient_id = item.get("patient_id")
                if patient_id is None:
                    continue
                patient_uuid = UUID(str(patient_id))
                try:
                    definition = await resolve_telemetry_definition(self.db, tenant_id, slug)
                except UnknownTelemetrySlugError as e:
                    logger.warning("telemetry row skipped: %s", e)
                    continue

                external_ref = str(item.get("device_id") or "imported")
                cache_key = (patient_uuid, external_ref)
                device_uuid = device_cache.get(cache_key)
                if device_uuid is None:
                    device_uuid = await resolve_telemetry_device(
                        self.db,
                        tenant_id=tenant_id,
                        patient_id=patient_uuid,
                        source="restore",
                        external_ref=external_ref,
                    )
                    device_cache[cache_key] = device_uuid

                row = TelemetryDataModel(
                    **build_telemetry_row(
                        tenant_id=tenant_id,
                        patient_id=patient_uuid,
                        device_id=device_uuid,
                        timestamp=_parse_dt(item.get("timestamp")) or datetime.now(UTC),
                        definition=definition,
                        value=float(value),
                        unit=item.get("unit"),
                    )
                )
                if item.get("raw_value") is not None:
                    row.raw_value = float(item["raw_value"])
                if item.get("raw_unit") is not None:
                    row.raw_unit = item["raw_unit"]
                self.db.add(row)
                count += 1
            except Exception as e:
                logger.warning(f"telemetry row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_integrations(
        self, payload: list[dict[str, Any]], tenant_id: UUID, id_remap: dict[str, str]
    ) -> tuple[int, list[str]]:
        warnings: list[str] = []
        if not settings.HA_DATA_KEY:
            warnings.append("HA_DATA_KEY not set; imported integration secrets will not decrypt.")
        count = 0
        for item in payload:
            try:
                source_id = item.get("id")
                source_uuid = _uuid(source_id)
                existing = None
                if source_uuid:
                    res = await self.db.execute(
                        select(UserIntegration).where(UserIntegration.id == source_uuid)
                    )
                    existing = res.scalar_one_or_none()
                patient_id = _uuid(item.get("patient_id"))
                if patient_id and str(item.get("patient_id")) in id_remap:
                    patient_id = _uuid(id_remap[str(item.get("patient_id"))])
                if existing and existing.tenant_id == tenant_id:
                    await self.db.execute(
                        sa_update(UserIntegration)
                        .where(UserIntegration.id == existing.id)
                        .values(
                            status=item.get("status"),
                            access_token=item.get("access_token"),
                            refresh_token=item.get("refresh_token"),
                            expires_at=_parse_dt(item.get("expires_at")),
                            scopes=item.get("scopes"),
                            user_config=item.get("user_config"),
                            instance_name=item.get("instance_name"),
                        )
                    )
                else:
                    from app.models.enums import IntegrationStatus

                    status_val = item.get("status")
                    try:
                        status_enum = (
                            IntegrationStatus(status_val)
                            if status_val
                            else IntegrationStatus.PENDING
                        )
                    except ValueError:
                        status_enum = IntegrationStatus.PENDING
                    integ = UserIntegration(
                        tenant_id=tenant_id,
                        user_id=_uuid(item.get("user_id")) or uuid4(),
                        patient_id=patient_id or uuid4(),
                        provider=item.get("provider") or "unknown",
                        status=status_enum,
                        access_token=item.get("access_token"),
                        refresh_token=item.get("refresh_token"),
                        expires_at=_parse_dt(item.get("expires_at")),
                        scopes=item.get("scopes"),
                        provider_account_id=item.get("provider_account_id"),
                        instance_name=item.get("instance_name"),
                        is_debug_enabled=bool(item.get("is_debug_enabled")),
                        user_config=item.get("user_config"),
                    )
                    self.db.add(integ)
                count += 1
            except Exception as e:
                logger.warning(f"integration row skipped: {e}")
        await self.db.flush()
        return count, warnings

    async def _restore_triggers(
        self, payload: list[dict[str, Any]], tenant_id: UUID, id_remap: dict[str, str]
    ) -> int:
        from app.models.enums import NotificationType, TriggerType

        count = 0
        for item in payload:
            try:
                pid = item.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                patient_uuid = _uuid(pid)
                if not patient_uuid:
                    continue
                try:
                    tt = TriggerType(item.get("trigger_type"))
                    nt = NotificationType(item.get("notification_type"))
                except (ValueError, TypeError):
                    continue
                trig = NotificationTrigger(
                    tenant_id=tenant_id,
                    patient_id=patient_uuid,
                    trigger_type=tt,
                    notification_type=nt,
                    config=item.get("config") or {},
                    title=item.get("title") or "Imported trigger",
                    body=item.get("body"),
                    enabled=bool(item.get("enabled", True)),
                    next_trigger=_parse_dt(item.get("next_trigger")),
                    reference_id=_uuid(item.get("reference_id")),
                )
                self.db.add(trig)
                count += 1
            except Exception as e:
                logger.warning(f"trigger row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_examinations(
        self, payload: list[dict[str, Any]], tenant_id: UUID, id_remap: dict[str, str]
    ) -> int:
        count = 0
        for item in payload:
            try:
                pid = item.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                # category_concept_id: prefer a concept that was just imported
                # (recorded in id_remap by _restore_concepts); else resolve by
                # slug against the target's visible taxonomy (covers the common
                # case of an exam pointing at a global/seeded concept we did
                # not export). Accepts the `category_id`/`category_details`
                # alias keys from older backups, plus the `category_concept_id`/
                # `category_concept` keys.
                category_concept_id = await self._resolve_concept_fk(
                    item.get("category_concept_id", item.get("category_id")),
                    (item.get("category_concept") or item.get("category_details") or {}).get(
                        "slug"
                    ),
                    ConceptKind.EXAMINATION_CATEGORY,
                    tenant_id,
                    id_remap,
                )
                # organization_id: remap if the org was created during FHIR
                # restore; else carry through only if it exists in-tenant.
                org_id_raw = item.get("organization_id")
                organization_id = (
                    _uuid(id_remap.get(str(org_id_raw), org_id_raw)) if org_id_raw else None
                )
                exam = ExaminationModel(
                    tenant_id=tenant_id,
                    patient_id=_uuid(pid),
                    examination_date=_parse_date(item.get("examination_date")),
                    notes=item.get("notes"),
                    patient_notes=item.get("patient_notes"),
                    category_concept_id=category_concept_id,
                    organization_id=organization_id,
                    source_integration_id=_uuid(item.get("source_integration_id")),
                    external_id=item.get("external_id"),
                    auto_extract_metadata=bool(item.get("auto_extract_metadata", False)),
                    diagnoses=item.get("diagnoses"),
                    impressions=item.get("impressions"),
                    extraction_status=item.get("extraction_status"),
                )
                self.db.add(exam)
                count += 1
            except Exception as e:
                logger.warning(f"examination row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_clinical_events(
        self, payload: list[dict[str, Any]], tenant_id: UUID, id_remap: dict[str, str]
    ) -> int:
        from app.models.enums import ClinicalEventStatus
        from app.services.clinical.clinical_event_service import _occurrence_row

        count = 0
        imported_events: list[tuple[ClinicalEvent, list[dict[str, Any]]]] = []
        for item in payload:
            try:
                pid = item.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                try:
                    status = ClinicalEventStatus(item.get("status", "ACTIVE"))
                except ValueError:
                    status = ClinicalEventStatus.ACTIVE
                ev = ClinicalEvent(
                    tenant_id=tenant_id,
                    patient_id=_uuid(pid) or uuid4(),
                    type_id=_uuid(item.get("type_id")),
                    status=status,
                    title=item.get("title") or "Imported event",
                    description=item.get("description"),
                    onset_date=_parse_dt(item.get("onset_date")),
                    resolved_date=_parse_dt(item.get("resolved_date")),
                    event_metadata=item.get("event_metadata"),
                )
                self.db.add(ev)
                imported_events.append((ev, item.get("occurrences") or []))
                count += 1
            except Exception as e:
                logger.warning(f"clinical_event row skipped: {e}")
        await self.db.flush()
        for ev, occurrences in imported_events:
            for occurrence in occurrences:
                row = _occurrence_row(ev.id, occurrence)
                if row is not None:
                    self.db.add(row)
        return count

    async def _restore_biomarker_catalog(self, payload: dict[str, Any], tenant_id: UUID) -> int:
        from app.schemas.biomarker import (
            BiomarkerCreate,
            CatalogImportPayload,
            UnitCreate,
        )
        from app.services.catalog.catalog_import_service import CatalogImportService

        units = [
            UnitCreate(
                symbol=u["symbol"],
                name=u["name"],
                quantity_type=u.get("quantity_type", "OTHER"),
            )
            for u in payload.get("units", [])
        ]
        biomarkers = []
        for b in payload.get("biomarkers", []):
            try:
                biomarkers.append(
                    BiomarkerCreate(
                        slug=b["slug"],
                        name=b["name"],
                        coding_system=b.get("coding_system", "loinc"),
                        code=b.get("code"),
                        category=b.get("category"),
                        class_concept_slug=b.get("class_concept_slug"),
                        aliases=b.get("aliases", []),
                        info=b.get("info"),
                        reference_range_min=b.get("reference_range_min"),
                        reference_range_max=b.get("reference_range_max"),
                        is_telemetry=b.get("is_telemetry", False),
                        preferred_unit_symbol=b.get("preferred_unit_symbol"),
                    )
                )
            except Exception as e:
                logger.warning(f"biomarker skipped: {e}")
        cat_payload = CatalogImportPayload(
            metadata=None,
            units=units,
            biomarkers=biomarkers,
        )
        svc = CatalogImportService(self.db)
        stats = await svc.import_catalog(cat_payload)
        return stats.get("biomarkers_added", 0) + stats.get("biomarkers_updated", 0)

    async def _restore_clinical_event_types(self, payload: dict[str, Any], tenant_id: UUID) -> int:
        from app.models.concept_model import Concept, ConceptKindTag
        from app.models.enums import ConceptKind, ConceptStatus
        from app.services.catalog.concept_service import concepts_with_kind

        count = 0
        # Event categories are now Concepts (kind=event_category). Upsert by slug.
        for c in payload.get("categories", []):
            try:
                slug = c.get("slug")
                if not slug:
                    continue
                res = await self.db.execute(
                    select(Concept).where(
                        Concept.slug == slug,
                        concepts_with_kind(ConceptKind.EVENT_CATEGORY),
                        or_(
                            Concept.tenant_id == tenant_id,
                            Concept.tenant_id.is_(None),
                        ),
                    )
                )
                if not res.scalar_one_or_none():
                    new_cat = Concept(
                        tenant_id=tenant_id,
                        name=c.get("name") or slug,
                        slug=slug,
                        primary_kind=ConceptKind.EVENT_CATEGORY,
                        description=c.get("description"),
                        icon=c.get("icon"),
                        color=c.get("color"),
                        status=ConceptStatus.ACTIVE,
                    )
                    new_cat.kind_tags.append(ConceptKindTag(kind=ConceptKind.EVENT_CATEGORY))
                    self.db.add(new_cat)
                    count += 1
            except Exception as e:
                logger.warning(f"category skipped: {e}")
        for t in payload.get("types", []):
            try:
                res = await self.db.execute(
                    select(ClinicalEventType).where(ClinicalEventType.slug == t.get("slug"))
                )
                if not res.scalar_one_or_none():
                    self.db.add(
                        ClinicalEventType(
                            tenant_id=tenant_id,
                            name=t.get("name") or t.get("slug"),
                            slug=t.get("slug"),
                            description=t.get("description"),
                            icon=t.get("icon"),
                            color=t.get("color"),
                            metadata_schema=t.get("metadata_schema"),
                        )
                    )
                    count += 1
            except Exception as e:
                logger.warning(f"event type skipped: {e}")
        await self.db.flush()
        return count

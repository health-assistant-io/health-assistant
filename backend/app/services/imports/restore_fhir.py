# ruff: noqa: E501,SIM102 -- long immutable strings; reflow when touched
"""FHIR bundle restore (create/update/delete verbs per resource)."""

import logging

from app.services.imports.types import (
    UUID,
    Any,
    BundleRestoreResult,
    FHIRImportConfig,
    Observation,
    _model_for_type,
    _uuid,
    fhir_to_orm,
    validate_bundle,
)

logger = logging.getLogger(__name__)


class RestoreFhirMixin:
    # ---------------- FHIR bundle restore ----------------

    async def restore_fhir_bundle(
        self,
        bundle: dict[str, Any],
        tenant_id: UUID,
        validate: bool = True,
        config: FHIRImportConfig | None = None,
        actor_user_id: UUID | None = None,
        source_job_id: UUID | None = None,
    ) -> BundleRestoreResult:
        created: dict[str, int] = {}
        updated: dict[str, int] = {}
        deleted: dict[str, int] = {}
        skipped: dict[str, int] = {}
        errors: list[str] = []
        warnings: list[str] = []
        id_remap: dict[str, str] = {}
        imported_obs_ids: list[UUID] = []

        if validate:
            ok, verrors = validate_bundle(bundle)
            if not ok:
                errors.extend(verrors)
                return BundleRestoreResult(
                    created=created,
                    updated=updated,
                    deleted=deleted,
                    skipped=skipped,
                    errors=errors,
                    warnings=warnings,
                    id_remap=id_remap,
                )

        entries = bundle.get("entry", [])
        if bundle.get("resourceType") == "Bundle" and not entries:
            return BundleRestoreResult(
                created=created,
                updated=updated,
                deleted=deleted,
                skipped=skipped,
                errors=errors,
                warnings=warnings,
                id_remap=id_remap,
            )

        if bundle.get("resourceType") != "Bundle":
            entries = [{"resource": bundle}]

        # G11: build a {id → resourceType} index once for the whole bundle so
        # _apply_remap can resolve ambiguous urn:uuid references (sender/recipient)
        # via bundle look-ahead.
        urn_type_index: dict[str, str] = {}
        for entry in entries:
            res = entry.get("resource") or {}
            rid = res.get("id")
            if rid:
                urn_type_index[str(rid)] = res.get("resourceType", "")

        # G9: cross-tenant collision warnings accumulated by _resolve_id.
        self._collision_warnings: list[str] = []

        for entry in entries:
            resource = entry.get("resource") or {}
            rt = resource.get("resourceType")
            if not rt:
                errors.append("Entry missing resource.resourceType; skipped")
                continue

            if rt == "DocumentReference":
                # We skip DocumentReference because Health Assistant exports it for FHIR
                # completeness, but actually restores documents via the nonfhir/documents.json sidecar.
                warnings.append(f"Skipped {rt} (handled via documents.json sidecar if present).")
                continue

            # G7/I4: honor entry.request.method + ifNoneExist. A bundle authored
            # as a transaction with {"request": {"method": "PUT", "url": "Type/id"}}
            # is now respected (update-if-exists / create-with-id), POST + ifNoneExist
            # is a conditional create, DELETE soft-deletes. Missing request block
            # defaults to POST (create-new) — the historical behaviour.
            request_block = entry.get("request") or {}
            method = (request_block.get("method") or "POST").upper()
            request_url = request_block.get("url")
            if_none_exist = request_block.get("ifNoneExist")

            # Per-resource validation happens inside fhir_to_orm() (via
            # fhir.resources); an invalid resource raises FhirSerializationError
            # which is caught below → skipped + logged (skip-and-log policy).
            try:
                stats_delta, obs_id = await self._restore_one_fhir_resource(
                    rt,
                    resource,
                    tenant_id,
                    id_remap,
                    method=method,
                    request_url=request_url,
                    if_none_exist=if_none_exist,
                    actor_user_id=actor_user_id,
                    source_job_id=source_job_id,
                    urn_type_index=urn_type_index,
                )
                if stats_delta == "created":
                    created[rt] = created.get(rt, 0) + 1
                elif stats_delta == "updated":
                    updated[rt] = updated.get(rt, 0) + 1
                elif stats_delta == "deleted":
                    deleted[rt] = deleted.get(rt, 0) + 1
                elif stats_delta == "skipped_conditional":
                    skipped[rt] = skipped.get(rt, 0) + 1
                    warnings.append(
                        f"Conditional create skipped for {rt}: ifNoneExist matched an existing resource"
                    )
                elif stats_delta in ("skipped_unsupported", "skipped_idempotent"):
                    if stats_delta == "skipped_unsupported":
                        w = f"Skipped unsupported FHIR resource type: {rt}"
                        if w not in warnings:
                            warnings.append(w)
                    # skipped_idempotent (DELETE on missing id) is silent — FHIR DELETE is idempotent
                if obs_id and rt == "Observation":
                    imported_obs_ids.append(obs_id)
            except Exception as e:
                logger.exception(f"Failed to restore {rt}")
                errors.append(f"{rt}: {e}")

        # Deduplicate DocumentReference warnings
        doc_ref_warning = (
            "Skipped DocumentReference (handled via documents.json sidecar if present)."
        )
        doc_ref_count = warnings.count(doc_ref_warning)
        if doc_ref_count > 1:
            warnings = [w for w in warnings if w != doc_ref_warning]
            warnings.append(doc_ref_warning)

        # Run biomarker mapping for newly imported observations
        if imported_obs_ids:
            if self._job_id:
                await self._update_progress(
                    self._job_id,
                    52,
                    message=f"Mapping {len(imported_obs_ids)} biomarker observation(s)",
                )
            try:
                from sqlalchemy import select

                from app.services.fhir.fhir_service import map_observations_to_biomarkers

                # We need to load the observations from DB
                res = await self.db.execute(
                    select(Observation).where(Observation.id.in_(imported_obs_ids))
                )
                obs_to_map = res.scalars().all()

                if obs_to_map:
                    # By default we map to existing definitions.
                    # We can use use_ai_normalization from config to determine if we should send unknowns to LLM
                    auto_map = getattr(config, "auto_map_biomarkers", True) if config else True
                    use_ai = getattr(config, "use_ai_normalization", False) if config else False

                    if auto_map:
                        # map_observations_to_biomarkers does basic string/code mapping
                        # If use_ai is enabled, DO NOT auto-create missing entries yet so the AI can handle them
                        await map_observations_to_biomarkers(
                            self.db, obs_to_map, auto_create_missing=not use_ai
                        )

                        if use_ai:
                            unmapped = [o for o in obs_to_map if not o.biomarker_id]
                            if unmapped:
                                from app.ai.pipeline.service import (
                                    MedicalProcessingService,
                                )
                                from app.ai.providers.service import AIProviderService
                                from app.ai.schemas.nlp import UnknownBiomarkerExtract

                                logger.info(
                                    "AI normalization: resolving %d unmapped observation(s) via NLP task",
                                    len(unmapped),
                                )
                                if self._job_id:
                                    await self._update_progress(
                                        self._job_id,
                                        55,
                                        message="AI normalization: generating biomarker definitions",
                                    )

                                ai_service = AIProviderService(self.db)
                                nlp_extractor = await ai_service.get_nlp_extractor(tenant_id)
                                med_service = MedicalProcessingService(self.db)

                                unknown_bios: list[Any] = []
                                seen_names: set = set()
                                for o in unmapped:
                                    text = o.code.get("text") or next(
                                        (
                                            c.get("display") or c.get("code")
                                            for c in o.code.get("coding", [])
                                        ),
                                        "Unknown",
                                    )
                                    name_key = text.lower().strip()
                                    if (
                                        not name_key
                                        or name_key == "unknown"
                                        or name_key in seen_names
                                    ):
                                        continue
                                    seen_names.add(name_key)
                                    try:
                                        value = float(
                                            o.raw_value
                                            or (
                                                o.value_quantity.get("value")
                                                if o.value_quantity
                                                else 0
                                            )
                                        )
                                    except (TypeError, ValueError):
                                        value = 0.0
                                    unit_symbol = (
                                        o.value_quantity.get("unit") if o.value_quantity else None
                                    )
                                    unknown_bios.append(
                                        UnknownBiomarkerExtract(
                                            raw_name=text,
                                            value=value,
                                            unit_symbol=unit_symbol or "",
                                        )
                                    )

                                if unknown_bios:
                                    slug_map: dict[str, str] = {}
                                    await med_service._process_unknown_biomarkers(
                                        unknown_bios, nlp_extractor, tenant_id, slug_map
                                    )
                                    await self.db.flush()
                                    logger.info(
                                        "AI normalization: created %d definition(s) (%d new slugs)",
                                        len(unknown_bios),
                                        len(slug_map),
                                    )

                                    await map_observations_to_biomarkers(self.db, unmapped)

                    # Telemetry fan-out for newly mapped observations
                    from app.models.biomarker_model import BiomarkerDefinition
                    from app.models.telemetry_model import TelemetryDataModel
                    from app.services.telemetry.telemetry_ingest import (
                        build_telemetry_row,
                        resolve_telemetry_device,
                    )

                    # Ensure we have the biomarkers loaded
                    mapped_obs = [o for o in obs_to_map if o.biomarker_id]
                    if mapped_obs:
                        b_ids = {o.biomarker_id for o in mapped_obs}
                        b_res = await self.db.execute(
                            select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
                        )
                        b_dict = {b.id: b for b in b_res.scalars().all()}

                        telemetry_records = []
                        device_cache = {}
                        for o in mapped_obs:
                            b_def = b_dict.get(o.biomarker_id)
                            if b_def and b_def.is_telemetry:
                                val = (
                                    getattr(o, "normalized_value", None)
                                    or getattr(o, "raw_value", None)
                                    or (o.value_quantity.get("value") if o.value_quantity else None)
                                )
                                if (
                                    val is None
                                    or getattr(o, "patient_id", None) is None
                                    or o.tenant_id is None
                                ):
                                    continue
                                unit = None
                                if getattr(o, "value_quantity", None):
                                    unit = o.value_quantity.get("unit")

                                device_uuid = device_cache.get(o.patient_id)
                                if device_uuid is None:
                                    device_uuid = await resolve_telemetry_device(
                                        self.db,
                                        tenant_id=o.tenant_id,
                                        patient_id=o.patient_id,
                                        source="fhir_import",
                                        external_ref="fhir_import",
                                    )
                                    device_cache[o.patient_id] = device_uuid

                                telemetry_records.append(
                                    TelemetryDataModel(
                                        **build_telemetry_row(
                                            tenant_id=o.tenant_id,
                                            patient_id=o.patient_id,
                                            device_id=device_uuid,
                                            timestamp=o.effective_datetime,
                                            definition=b_def,
                                            value=float(val),
                                            unit=unit,
                                        )
                                    )
                                )

                        if telemetry_records:
                            self.db.add_all(telemetry_records)
                            await self.db.flush()

            except Exception as e:
                logger.exception("Failed to map biomarkers for imported observations")
                warnings.append(f"Failed to map biomarkers for imported observations: {e}")

        # G9: surface cross-tenant collision warnings collected by _resolve_id.
        warnings.extend(getattr(self, "_collision_warnings", []))

        return BundleRestoreResult(
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
            errors=list(set(errors)),
            warnings=warnings,
            id_remap=id_remap,
        )

    async def _restore_one_fhir_resource(
        self,
        rt: str,
        fhir_dict: dict[str, Any],
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        method: str = "POST",
        request_url: str | None = None,
        if_none_exist: str | None = None,
        actor_user_id: UUID | None = None,
        source_job_id: UUID | None = None,
        urn_type_index: dict[str, str] | None = None,
    ) -> tuple[str, UUID | None]:
        """Restore one FHIR entry, honoring the bundle's request verb.

        Verb routing (G7/I4):
        - ``DELETE``  → soft-delete by id (idempotent on missing).
        - ``PUT``     → update-if-exists, else create-with-the-supplied-id.
        - ``POST`` + ``ifNoneExist`` → conditional create (skip if match).
        - ``POST`` (default) → create-new (historical behaviour).

        G6: records a Provenance per successful created/updated/deleted entry
        (best-effort — never aborts the import).
        """
        method = (method or "POST").upper()
        model = _model_for_type(rt)

        # --- DELETE: soft-delete by id (idempotent on missing) ---
        if method == "DELETE":
            target_id_str = self._parse_request_id(request_url) or fhir_dict.get("id")
            if model is None or not hasattr(model, "deleted_at") or not target_id_str:
                return "skipped_unsupported", None
            ok = await self._soft_delete_by_id(model, tenant_id, target_id_str)
            action = "deleted" if ok else "skipped_idempotent"
            if ok:
                await self._record_import_provenance(
                    rt,
                    _uuid(target_id_str),
                    action,
                    tenant_id,
                    actor_user_id,
                    source_job_id,
                )
            return action, None

        # For PUT/POST we need a converter. Practitioner has no module-level
        # model entry but DOES have a converter (fhir_to_practitioner_orm).
        from app.services.fhir.fhir_converter import _TO_ORM

        if rt not in _TO_ORM:
            logger.warning(f"Unsupported resource type {rt}")
            return "skipped_unsupported", None

        # --- PUT: id comes from the request URL (FHIR spec); ensure the body carries it ---
        force_id: UUID | None = None
        remapped = fhir_dict
        if method == "PUT":
            url_id = self._parse_request_id(request_url)
            if url_id:
                remapped = dict(fhir_dict)  # don't mutate the caller's resource dict
                remapped["id"] = url_id
                force_id = _uuid(url_id)
            # fall through to the upsert path (update-if-exists, create-with-id-if-not)

        # --- POST + ifNoneExist: conditional create (skip if a match exists) ---
        if method == "POST" and if_none_exist and model is not None:
            existing_id = await self._conditional_find(model, rt, tenant_id, if_none_exist)
            if existing_id is not None:
                return "skipped_conditional", None
            # no match (or unsupported form) → fall through to create

        source_id = remapped.get("id")
        source_id_str = str(source_id) if source_id else None
        remapped = self._apply_remap(remapped, id_remap, urn_type_index=urn_type_index)
        orm_dict = fhir_to_orm(rt, remapped)
        orm_dict["tenant_id"] = tenant_id

        if rt == "Patient":
            action_str, target_id = (
                await self._upsert_patient(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Observation":
            action_str, target_id = await self._upsert_observation(
                orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
            )
        elif rt in ("MedicationStatement", "MedicationRequest"):
            # Both persist to the Medication table, distinguished by `intent`.
            action_str, target_id = (
                await self._upsert_medication(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "AllergyIntolerance":
            action_str, target_id = (
                await self._upsert_allergy(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "DiagnosticReport":
            action_str, target_id = (
                await self._upsert_diagnostic_report(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Organization":
            action_str, target_id = (
                await self._upsert_organization(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Practitioner":
            action_str, target_id = (
                await self._upsert_practitioner(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Condition":
            action_str, target_id = (
                await self._upsert_condition(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Encounter":
            action_str, target_id = (
                await self._upsert_encounter(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Device":
            action_str, target_id = (
                await self._upsert_device(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Communication":
            action_str, target_id = (
                await self._upsert_communication(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Provenance":
            action_str, target_id = (
                await self._upsert_provenance(
                    orm_dict, source_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        else:
            logger.warning(f"Unsupported resource type {rt} (no upsert branch)")
            return "skipped_unsupported", None

        # G6: record a Provenance for the created/updated entry. Derive the
        # target id from the upsert result (Observation returns it directly);
        # for other types, read it from id_remap when the entry carried a
        # bundle id. Best-effort — never aborts the import.
        if action_str in ("created", "updated") and target_id is None and source_id_str:
            target_id = _uuid(id_remap.get(source_id_str))
        if action_str in ("created", "updated"):
            await self._record_import_provenance(
                rt, target_id, action_str, tenant_id, actor_user_id, source_job_id
            )
        return action_str, target_id

    async def _record_import_provenance(
        self,
        rt: str,
        target_id: UUID | None,
        action: str,
        tenant_id: UUID,
        actor_user_id: UUID | None,
        source_job_id: UUID | None,
    ) -> None:
        """G6: record one Provenance per imported/updated entry (best-effort).

        The ``entity_inputs`` carry an ``ImportJob/<id>`` reference so the
        audit trail is self-describing (distinguishes bulk-import Provenance
        from facade-write Provenance). Never raises — ``record_provenance``
        itself is best-effort and this wrapper adds its own guard.
        """
        if target_id is None:
            logger.debug("Skipping import Provenance for %s (no target id)", rt)
            return
        from app.services.fhir.provenance_service import (
            RECORD_CREATE,
            RECORD_DELETE,
            RECORD_UPDATE,
            record_provenance,
        )

        activity = {
            "created": RECORD_CREATE,
            "updated": RECORD_UPDATE,
            "deleted": RECORD_DELETE,
        }.get(action)
        if activity is None:
            return
        entity_inputs = None
        if source_job_id is not None:
            entity_inputs = [
                {
                    "role": "source",
                    "what": {"reference": f"ImportJob/{source_job_id}"},
                }
            ]
        try:
            await record_provenance(
                self.db,
                target_resource_type=rt,
                target_id=target_id,
                activity=activity,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                entity_inputs=entity_inputs,
            )
        except Exception:
            logger.debug(
                "Import Provenance recording failed for %s/%s",
                rt,
                target_id,
                exc_info=True,
            )

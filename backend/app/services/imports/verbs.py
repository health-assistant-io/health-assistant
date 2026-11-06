"""Verb-routing helpers (G7/I4): conditional find, remap, resolve, core upserts."""

import logging

from app.services.imports.types import (
    FIELD_HINT_TO_TYPE,
    UTC,
    UUID,
    AllergyCategory,
    AllergyClinicalStatus,
    AllergyCriticality,
    AllergyIntolerance,
    Any,
    DiagnosticReport,
    Gender,
    Medication,
    MedicationStatus,
    Observation,
    OrganizationModel,
    Patient,
    _parse_date,
    _parse_dt,
    _uuid,
    coerce_patient_id,
    datetime,
    json,
    parse_qs,
    sa_update,
    select,
    uuid4,
)

logger = logging.getLogger(__name__)


class VerbRoutingMixin:
    # ---------------- verb-routing helpers (G7/I4) ----------------

    @staticmethod
    def _parse_request_id(url: str | None) -> str | None:
        """Extract the id from a FHIR request URL like ``Observation/abc``.

        Returns ``None`` for conditional URLs (``Observation?identifier=...``)
        or malformed input, so callers can fall back gracefully.
        """
        if not url:
            return None
        url = url.lstrip("/")
        if "?" in url or "=" in url:
            return None  # conditional form — no literal id
        parts = url.split("/")
        if len(parts) == 2 and parts[1]:
            return parts[1]
        return None

    async def _soft_delete_by_id(self, model: Any, tenant_id: UUID, resource_id_str: str) -> bool:
        """Soft-delete (set ``deleted_at``) a resource by id, tenant-scoped.

        Returns True if a row was updated, False if not found / already deleted.
        Only callable on models mixing in ``SoftDeleteMixin``.
        """
        rid = _uuid(resource_id_str)
        if rid is None or not hasattr(model, "deleted_at"):
            return False
        result = await self.db.execute(
            sa_update(model)
            .where(
                model.id == rid,
                model.tenant_id == tenant_id,
                model.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
        )
        return bool(getattr(result, "rowcount", 0) or 0)

    async def _conditional_find(
        self, model: Any, rt: str, tenant_id: UUID, if_none_exist: str
    ) -> UUID | None:
        """Best-effort conditional match for FHIR ``ifNoneExist``.

        Supported forms (commonly used by real clients):
        - ``identifier=<code>`` or ``identifier=<system>|<code>`` on **Patient** →
          matches the ``mrn`` column (tenant-scoped, not-deleted).

        Unsupported forms (any other resource type, or non-identifier params)
        log a warning and return None so the caller falls through to an
        unconditional create. This is honest: we surface the gap rather than
        silently treat "couldn't match" as "no match".
        """
        params = parse_qs(if_none_exist, keep_blank_values=True)
        if params.get("identifier"):
            ident = params["identifier"][0]
            # FHIR token form: "system|code" → take the code (after the pipe)
            code = ident.split("|", 1)[-1] if "|" in ident else ident
            code = code.strip()
            if not code:
                return None
            if rt == "Patient" and hasattr(model, "mrn"):
                not_deleted = model.deleted_at.is_(None) if hasattr(model, "deleted_at") else True
                res = await self.db.execute(
                    select(model.id).where(
                        model.tenant_id == tenant_id,
                        model.mrn == code,
                        not_deleted,
                    )
                )
                row = res.first()
                return row[0] if row else None
        logger.warning(
            "ifNoneExist query form not supported for %s: %r; creating unconditionally",
            rt,
            if_none_exist,
        )
        return None

    @staticmethod
    def _apply_remap(
        fhir_dict: dict[str, Any],
        id_remap: dict[str, str],
        urn_type_index: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Rewrite inter-resource references using the id_remap table.

        G11: routes bare ``urn:uuid:`` references to the correct resource type
        via :data:`FIELD_HINT_TO_TYPE`. Ambiguous hints (``sender``/``recipient``)
        are resolved by looking up the referenced id in ``urn_type_index`` (a
        bundle-wide ``{id → resourceType}`` map built once by
        ``restore_fhir_bundle``). Previously every bare urn:uuid defaulted to
        ``Patient`` unless the hint was ``performer``/``partOf``, so an
        ``Observation.encounter`` given as ``urn:uuid:abc`` was misrouted to
        ``Patient/<new>``.
        """
        if not id_remap:
            return dict(fhir_dict)
        d = json.loads(json.dumps(fhir_dict, default=str))
        urn_type_index = urn_type_index or {}

        def _resolve_type(field_hint: str, rid: str) -> str | None:
            """Determine the FHIR resource type for a bare urn:uuid reference."""
            mapped = FIELD_HINT_TO_TYPE.get(field_hint)
            if mapped is not None:
                return mapped
            # Ambiguous hint — look up the resource type from the bundle index.
            return urn_type_index.get(rid)

        def _remap_ref(obj: Any, field_hint: str) -> Any:
            if isinstance(obj, dict):
                ref = obj.get("reference")
                if isinstance(ref, str):
                    if "/" in ref:
                        prefix, rid = ref.split("/", 1)
                        if rid in id_remap:
                            return {"reference": f"{prefix}/{id_remap[rid]}"}
                    elif ref.startswith("urn:uuid:"):
                        rid = ref.replace("urn:uuid:", "")
                        if rid in id_remap:
                            prefix = _resolve_type(field_hint, rid) or "Patient"
                            return {"reference": f"{prefix}/{id_remap[rid]}"}
                return {k: _remap_ref(v, field_hint) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_remap_ref(x, field_hint) for x in obj]
            return obj

        for fld in (
            "subject",
            "patient",
            "partOf",
            "performer",
            "context",
            "encounter",
            "author",
            "device",
            "specimen",
            "sender",
            "recipient",
        ):
            if fld in d:
                d[fld] = _remap_ref(d[fld], fld)
        return d

    async def _resolve_id(
        self,
        model,
        source_id_str: str | None,
        tenant_id: UUID,
        *,
        force_id: UUID | None = None,
    ) -> tuple[UUID | None, UUID, str]:
        """Resolve whether an upsert is an update or a create.

        When ``force_id`` is supplied (PUT path) and no existing row matches,
        the create uses the supplied id instead of minting a fresh ``uuid4`` —
        this is what makes ``PUT Type/<id>`` create-with-id when the id is absent.

        G9: when the bundle id exists in ANOTHER tenant, the collision is no
        longer silent — a warning is appended to ``self._collision_warnings`` so
        the ImportJob result surfaces it (previously looked like 100% creation).
        """
        if source_id_str:
            source_uuid = _uuid(source_id_str)
            if source_uuid:
                res = await self.db.execute(select(model).where(model.id == source_uuid))
                existing = res.scalar_one_or_none()
                if existing:
                    if existing.tenant_id == tenant_id:
                        return source_uuid, source_uuid, "updated"
                    # G9: cross-tenant collision — mint a new id but surface a warning.
                    new_id = force_id or uuid4()
                    self._collision_warnings.append(
                        f"{model.__name__}/{source_id_str} already exists in tenant "
                        f"{existing.tenant_id}; created new with id {new_id} in tenant {tenant_id}"
                    )
                    return None, new_id, "created"
        return None, force_id or uuid4(), "created"

    async def _upsert_patient(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            Patient, source_id_str, tenant_id, force_id=force_id
        )
        mrn = (orm.get("mrn") or "").strip() or None
        if existing_id:
            await self.db.execute(
                sa_update(Patient)
                .where(Patient.id == existing_id)
                .values(
                    name=orm.get("name"),
                    gender=Gender(orm.get("gender", "UNKNOWN").upper())
                    if orm.get("gender")
                    else Gender.UNKNOWN,
                    birth_date=_parse_date(orm.get("birth_date")),
                    address=orm.get("address"),
                    telecom=orm.get("telecom"),
                    mrn=mrn,
                )
            )
            id_remap[source_id_str] = str(existing_id)
            return "updated"
        patient = Patient(
            id=new_id,
            tenant_id=tenant_id,
            name=orm.get("name") or [{"text": "Imported"}],
            gender=Gender(orm.get("gender", "UNKNOWN").upper())
            if orm.get("gender")
            else Gender.UNKNOWN,
            birth_date=_parse_date(orm.get("birth_date")),
            address=orm.get("address"),
            telecom=orm.get("telecom"),
            mrn=mrn,
        )
        self.db.add(patient)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_observation(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> tuple[str, UUID | None]:
        existing_id, new_id, _action = await self._resolve_id(
            Observation, source_id_str, tenant_id, force_id=force_id
        )

        # Hard value-shape contract (plan Step 5): validate value[x]
        # against the biomarker's value_type before persisting. The
        # validator is a no-op when biomarker_id is absent or the
        # biomarker can't be loaded.
        _bio_id = orm.get("biomarker_id")
        if _bio_id:
            from app.models.biomarker_model import BiomarkerDefinition
            from app.services.biomarkers.observation_value_validator import (
                validate_observation_payload,
            )

            _bio = await self.db.get(BiomarkerDefinition, _bio_id)
            try:
                validate_observation_payload(_bio, orm)
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "Import observation skipped — value contract violation for biomarker %s",
                    _bio_id,
                )
                return "skipped", None

        # Semantic Deduplication
        if not existing_id:
            from sqlalchemy import and_, select

            # Check for identical observation
            code_text = orm.get("code", {}).get("text")
            subject_ref = orm.get("subject", {}).get("reference")
            effective_dt = _parse_dt(orm.get("effective_datetime"))

            if code_text and subject_ref and effective_dt:
                stmt = select(Observation).where(
                    and_(
                        Observation.tenant_id == tenant_id,
                        Observation.subject["reference"].astext == subject_ref,
                        Observation.effective_datetime == effective_dt,
                    )
                )
                res = await self.db.execute(stmt)
                for existing_obs in res.scalars().all():
                    existing_code = existing_obs.code.get("text")
                    if existing_code == code_text:
                        # Found a match, merge instead of create
                        existing_id = existing_obs.id
                        break

        if existing_id:
            await self.db.execute(
                sa_update(Observation)
                .where(Observation.id == existing_id)
                .values(
                    status=orm.get("status") or "final",
                    code=orm.get("code"),
                    subject=orm.get("subject"),
                    patient_id=coerce_patient_id(None, orm.get("subject")),
                    effective_datetime=_parse_dt(orm.get("effective_datetime")),
                    value_quantity=orm.get("value_quantity"),
                    value_string=orm.get("value_string"),
                    # The converter emits snake_case value_codeable_concept;
                    # the ORM column is camelCase value_codeable_concept.
                    # Pre-fix this was silently dropped on round-trip.
                    value_codeable_concept=orm.get("value_codeable_concept"),
                    reference_range=orm.get("reference_range"),
                    performer=orm.get("performer"),
                    interpretation=orm.get("interpretation"),
                    component=orm.get("component"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated", existing_id
        obs = Observation(
            id=new_id,
            tenant_id=tenant_id,
            status=orm.get("status") or "final",
            code=orm.get("code") or {"text": "unknown"},
            subject=orm.get("subject") or {"reference": "Patient/unknown"},
            patient_id=coerce_patient_id(None, orm.get("subject")),
            effective_datetime=_parse_dt(orm.get("effective_datetime")),
            value_quantity=orm.get("value_quantity"),
            value_string=orm.get("value_string"),
            value_codeable_concept=orm.get("value_codeable_concept"),
            reference_range=orm.get("reference_range"),
            performer=orm.get("performer"),
            interpretation=orm.get("interpretation"),
            component=orm.get("component"),
        )
        self.db.add(obs)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created", new_id

    async def _upsert_medication(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            Medication, source_id_str, tenant_id, force_id=force_id
        )
        patient_id = _uuid(orm.get("patient_id"))
        try:
            status = MedicationStatus(orm.get("status", "ACTIVE").upper())
        except ValueError:
            status = MedicationStatus.ACTIVE
        if existing_id:
            await self.db.execute(
                sa_update(Medication)
                .where(Medication.id == existing_id)
                .values(
                    status=status,
                    code=orm.get("code"),
                    patient_id=patient_id,
                    start_date=_parse_date(orm.get("start_date")),
                    end_date=_parse_date(orm.get("end_date")),
                    dosage=orm.get("dosage"),
                    frequency=orm.get("frequency"),
                    reason=orm.get("reason"),
                    note=orm.get("note"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        med = Medication(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id or uuid4(),
            status=status,
            code=orm.get("code") or {"text": "unknown"},
            start_date=_parse_date(orm.get("start_date")),
            end_date=_parse_date(orm.get("end_date")),
            dosage=orm.get("dosage"),
            frequency=orm.get("frequency"),
            reason=orm.get("reason"),
            note=orm.get("note"),
        )
        self.db.add(med)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_allergy(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            AllergyIntolerance, source_id_str, tenant_id, force_id=force_id
        )
        patient_id = _uuid(orm.get("patient_id"))
        try:
            clinical = AllergyClinicalStatus(orm.get("clinical_status", "ACTIVE").upper())
        except ValueError:
            clinical = AllergyClinicalStatus.ACTIVE
        category = None
        if orm.get("category"):
            try:
                category = AllergyCategory(orm.get("category").upper())
            except ValueError:
                category = None
        criticality = None
        if orm.get("criticality"):
            try:
                criticality = AllergyCriticality(orm.get("criticality").upper())
            except ValueError:
                criticality = None
        if existing_id:
            await self.db.execute(
                sa_update(AllergyIntolerance)
                .where(AllergyIntolerance.id == existing_id)
                .values(
                    clinical_status=clinical,
                    verification_status=orm.get("verification_status") or "confirmed",
                    category=category,
                    criticality=criticality,
                    code=orm.get("code"),
                    patient_id=patient_id,
                    onset_date=_parse_dt(orm.get("onset_date")),
                    note=orm.get("note"),
                    reactions=orm.get("reactions"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        allergy = AllergyIntolerance(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id or uuid4(),
            clinical_status=clinical,
            verification_status=orm.get("verification_status") or "confirmed",
            category=category,
            criticality=criticality,
            code=orm.get("code") or {"text": "unknown"},
            onset_date=_parse_dt(orm.get("onset_date")),
            note=orm.get("note"),
            reactions=orm.get("reactions"),
        )
        self.db.add(allergy)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_diagnostic_report(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            DiagnosticReport, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(DiagnosticReport)
                .where(DiagnosticReport.id == existing_id)
                .values(
                    status=orm.get("status") or "final",
                    code=orm.get("code"),
                    subject=orm.get("subject"),
                    patient_id=coerce_patient_id(None, orm.get("subject")),
                    effective_datetime=_parse_dt(orm.get("effective_datetime")),
                    issued=_parse_dt(orm.get("issued")),
                    performer=orm.get("performer"),
                    conclusion=orm.get("conclusion"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        dr = DiagnosticReport(
            id=new_id,
            tenant_id=tenant_id,
            status=orm.get("status") or "final",
            code=orm.get("code") or {"text": "unknown"},
            subject=orm.get("subject") or {"reference": "Patient/unknown"},
            patient_id=coerce_patient_id(None, orm.get("subject")),
            effective_datetime=_parse_dt(orm.get("effective_datetime")),
            issued=_parse_dt(orm.get("issued")),
            performer=orm.get("performer"),
            conclusion=orm.get("conclusion"),
        )
        self.db.add(dr)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_organization(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            OrganizationModel, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(OrganizationModel)
                .where(OrganizationModel.id == existing_id)
                .values(
                    name=orm.get("name") or "Imported",
                    type=orm.get("type"),
                    telecom=orm.get("telecom"),
                    address=orm.get("address"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        org = OrganizationModel(
            id=new_id,
            tenant_id=tenant_id,
            name=orm.get("name") or "Imported Organization",
            type=orm.get("type"),
            telecom=orm.get("telecom"),
            address=orm.get("address"),
        )
        self.db.add(org)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_practitioner(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        from app.models.doctor_model import DoctorModel
        from app.services.clinical.doctor_service import _resolve_specialty_concept

        existing_id, new_id, _action = await self._resolve_id(
            DoctorModel, source_id_str, tenant_id, force_id=force_id
        )
        specialty_concept_id = await _resolve_specialty_concept(
            self.db, orm.get("specialty"), tenant_id=tenant_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(DoctorModel)
                .where(DoctorModel.id == existing_id)
                .values(
                    name=orm.get("name") or "Imported",
                    specialty_concept_id=specialty_concept_id,
                    license_number=orm.get("license_number"),
                    email=orm.get("email"),
                    phone=orm.get("phone"),
                    telecom=orm.get("telecom"),
                    address=orm.get("address"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        doc = DoctorModel(
            id=new_id,
            tenant_id=tenant_id,
            name=orm.get("name") or "Imported Doctor",
            specialty_concept_id=specialty_concept_id,
            license_number=orm.get("license_number"),
            email=orm.get("email"),
            phone=orm.get("phone"),
            telecom=orm.get("telecom"),
            address=orm.get("address"),
        )
        self.db.add(doc)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

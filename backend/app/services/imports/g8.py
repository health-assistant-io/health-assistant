"""G8 upserts: the five resource types added in phase 6.2."""

from app.services.imports.types import (
    UTC,
    UUID,
    Any,
    ClinicalEvent,
    CommunicationModel,
    DeviceModel,
    ExaminationModel,
    ProvenanceModel,
    _parse_date,
    _parse_dt,
    _uuid,
    datetime,
    sa_update,
    uuid4,
)


class G8UpsertsMixin:
    # ---------------- G8: the 5 resource types added in Phase 6.2 ----------------

    async def _upsert_condition(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            ClinicalEvent, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(ClinicalEvent)
                .where(ClinicalEvent.id == existing_id)
                .values(
                    status=orm.get("status"),
                    title=orm.get("title") or "Untitled Condition",
                    description=orm.get("description"),
                    onset_date=_parse_dt(orm.get("onset_date")),
                    resolved_date=_parse_dt(orm.get("resolved_date")),
                    code=orm.get("code"),
                    coding_system=orm.get("coding_system"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        patient_id = _uuid(orm.get("patient_id"))
        ev = ClinicalEvent(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id or uuid4(),
            status=orm.get("status"),
            title=orm.get("title") or "Untitled Condition",
            description=orm.get("description"),
            onset_date=_parse_dt(orm.get("onset_date")),
            resolved_date=_parse_dt(orm.get("resolved_date")),
            code=orm.get("code"),
            coding_system=orm.get("coding_system"),
        )
        self.db.add(ev)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_encounter(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        # ExaminationModel declares tenant_id manually (NOT via TenantMixin) but it's NOT NULL.
        existing_id, new_id, _action = await self._resolve_id(
            ExaminationModel, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(ExaminationModel)
                .where(ExaminationModel.id == existing_id)
                .values(
                    examination_date=_parse_date(orm.get("examination_date")),
                    organization_id=_uuid(orm.get("organization_id")),
                    notes=orm.get("notes"),
                    diagnoses=orm.get("diagnoses"),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        patient_id = _uuid(orm.get("patient_id"))
        exam = ExaminationModel(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            examination_date=_parse_date(orm.get("examination_date")),
            organization_id=_uuid(orm.get("organization_id")),
            notes=orm.get("notes"),
            diagnoses=orm.get("diagnoses"),
        )
        self.db.add(exam)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_device(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            DeviceModel, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(DeviceModel)
                .where(DeviceModel.id == existing_id)
                .values(
                    identifier=orm.get("identifier"),
                    device_name=orm.get("device_name"),
                    type=orm.get("type"),
                    manufacturer=orm.get("manufacturer"),
                    model_number=orm.get("model_number"),
                    serial_number=orm.get("serial_number"),
                    status=orm.get("status") or "active",
                    owner_integration_id=_uuid(orm.get("owner_integration_id")),
                    patient_id=_uuid(orm.get("patient_id")),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        dev = DeviceModel(
            id=new_id,
            tenant_id=tenant_id,
            identifier=orm.get("identifier"),
            device_name=orm.get("device_name"),
            type=orm.get("type"),
            manufacturer=orm.get("manufacturer"),
            model_number=orm.get("model_number"),
            serial_number=orm.get("serial_number"),
            status=orm.get("status") or "active",
            owner_integration_id=_uuid(orm.get("owner_integration_id")),
            patient_id=_uuid(orm.get("patient_id")),
        )
        self.db.add(dev)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_communication(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        existing_id, new_id, _action = await self._resolve_id(
            CommunicationModel, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(CommunicationModel)
                .where(CommunicationModel.id == existing_id)
                .values(
                    status=orm.get("status") or "completed",
                    category=orm.get("category"),
                    priority=orm.get("priority"),
                    topic=orm.get("topic"),
                    payload=orm.get("payload"),
                    sent=_parse_dt(orm.get("sent")),
                    received=_parse_dt(orm.get("received")),
                    sender=orm.get("sender"),
                    recipient=orm.get("recipient"),
                    subject_patient_id=_uuid(orm.get("subject_patient_id")),
                    encounter_id=_uuid(orm.get("encounter_id")),
                )
            )
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        comm = CommunicationModel(
            id=new_id,
            tenant_id=tenant_id,
            status=orm.get("status") or "completed",
            category=orm.get("category"),
            priority=orm.get("priority"),
            topic=orm.get("topic"),
            payload=orm.get("payload"),
            sent=_parse_dt(orm.get("sent")),
            received=_parse_dt(orm.get("received")),
            sender=orm.get("sender"),
            recipient=orm.get("recipient"),
            subject_patient_id=_uuid(orm.get("subject_patient_id")),
            encounter_id=_uuid(orm.get("encounter_id")),
        )
        self.db.add(comm)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

    async def _upsert_provenance(
        self,
        orm: dict[str, Any],
        source_id_str: str | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
        *,
        force_id: UUID | None = None,
    ) -> str:
        # Provenance is immutable (no VersionedMixin, no SoftDeleteMixin). Upsert
        # is create-only: if the id already exists, leave the original untouched
        # and count as "updated" (idempotent no-op) rather than overwriting history.
        existing_id, new_id, _action = await self._resolve_id(
            ProvenanceModel, source_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            if source_id_str:
                id_remap[source_id_str] = str(existing_id)
            return "updated"
        prov = ProvenanceModel(
            id=new_id,
            tenant_id=tenant_id,
            target=orm.get("target") or [],
            recorded=_parse_dt(orm.get("recorded")) or datetime.now(UTC),
            activity=orm.get("activity"),
            agent=orm.get("agent") or [],
            entity=orm.get("entity"),
        )
        self.db.add(prov)
        await self.db.flush()
        if source_id_str:
            id_remap[source_id_str] = str(new_id)
        return "created"

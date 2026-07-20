from sqlalchemy import Column, String, Text, ForeignKey, DateTime, Enum, Index, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.models.enums import ClinicalEventStatus, CodingSystem, ScheduleKind
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat


def _enum_values(enum_cls):
    """Persist the enum ``.value`` (not the member name) — matches the pattern
    in ``concept_model`` for lowercase-value enums like ``ScheduleKind``."""
    return [e.value for e in enum_cls]


class ClinicalEventType(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "clinical_event_types"

    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(JSONB, nullable=True)  # { "type": "lucide", "value": "Activity" }
    color = Column(String(50), nullable=True)
    metadata_schema = Column(JSONB, nullable=True)  # { "fields": [...] }

    # Phase 4a journey-template fields (all optional/nullable). Drive the
    # ClinicalEventEngine's behavior (current phase, milestones, overdue flag).
    severity_scale = Column(JSONB, nullable=True)
    phases = Column(JSONB, nullable=True)
    milestones = Column(JSONB, nullable=True)
    default_duration_days = Column(Integer, nullable=True)

    # Phase 4 calendar-rendering hint. Declares how instances of this type should
    # be rendered in calendar/schedule surfaces (state/range/recurring/point).
    # Frontend adapter reads this instead of inferring from status.
    #
    # Phase 8a: NOT NULL with a server default of STATE (the safe "never per-day
    # expansion" behavior). Existing NULL rows were backfilled to STATE by the
    # Phase 8a migration before the constraint was added.
    schedule_kind = Column(
        Enum(ScheduleKind, values_callable=_enum_values),
        nullable=False,
        default=ScheduleKind.STATE,
        server_default=ScheduleKind.STATE.value,
    )

    # Phase 8e: NOT NULL. `ondelete="RESTRICT"` — you can't delete a
    # Concept that types still reference (the admin must reassign the types
    # first). Replaces the previous `SET NULL` which contradicted the NOT
    # NULL constraint. Mirrors the Phase 8a tightening on `schedule_kind`.
    #
    # The Phase 8e migration backfills any NULL rows to the seeded system
    # "General" concept (slug `general-event`) before adding the constraint.
    category_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # Nullable for global event types
        index=True,
    )

    # Relationships
    category_concept = relationship(
        "Concept",
        foreign_keys="[ClinicalEventType.category_concept_id]",
        lazy="selectin",
    )
    events = relationship("ClinicalEvent", back_populates="type_entity")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "metadata_schema": self.metadata_schema,
            "severity_scale": self.severity_scale,
            "phases": self.phases,
            "milestones": self.milestones,
            "default_duration_days": self.default_duration_days,
            "schedule_kind": self.schedule_kind.value if self.schedule_kind else None,
            "category_concept_id": str(self.category_concept_id)
            if self.category_concept_id
            else None,
            "category_concept": self.category_concept.to_dict()
            if self.category_concept
            else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
        }


class EventExaminationLink(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_examination_links"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    examination_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason = Column(
        Text, nullable=True
    )  # Why this examination is related to this event

    # Relationships
    event = relationship("ClinicalEvent", back_populates="examination_links")
    examination = relationship("ExaminationModel", backref="event_links")


class EventObservationLink(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_observation_links"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notes = Column(Text, nullable=True)

    # Relationships
    event = relationship("ClinicalEvent", back_populates="observation_links")
    observation = relationship("Observation", backref="event_links")


class ClinicalEventOccurrence(Base, UUIDMixin, TimestampMixin):
    """A discrete episode/point-in-time within a :class:`ClinicalEvent` journey.

    Promotes the legacy untyped ``ClinicalEvent.occurrences`` JSONB array into a
    queryable first-class model: each row is one occurrence (e.g. a specific
    migraine with an intensity and a body site). Anatomy is optionally linked
    to ``anatomy_structures`` so occurrences can be sliced by body region.

    The legacy ``ClinicalEvent.occurrences`` JSONB column is retained for one
    cycle as read-back fallback; :meth:`ClinicalEvent.to_dict` sources from
    ``occurrence_links`` when present.
    """

    __tablename__ = "clinical_event_occurrences"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, index=True
    )
    title = Column(String(255), nullable=True)
    severity = Column(String(50), nullable=True)  # 'mild' | 'moderate' | 'severe'
    intensity = Column(Integer, nullable=True)  # e.g. 1..10 for pain-style types
    notes = Column(Text, nullable=True)
    anatomy_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("anatomy_structures.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata_ = Column("metadata", JSONB, nullable=True, default=dict)

    # Relationships
    event = relationship("ClinicalEvent", back_populates="occurrence_links")
    anatomy = relationship("AnatomyStructure", lazy="selectin")

    def to_summary(self) -> dict:
        """Serialize to a dict that is backward-compatible with the legacy
        ``occurrences`` JSONB shape (``date``/``intensity``/``notes`` keys)
        while also exposing the richer model fields."""
        occurred_iso = self.occurred_at.isoformat() if self.occurred_at else None
        return {
            "id": str(self.id) if self.id else None,
            # Legacy-compatible keys (what ClinicalEvent.occurrences held).
            "date": occurred_iso,
            "intensity": self.intensity,
            "notes": self.notes,
            # Richer fields.
            "occurred_at": occurred_iso,
            "title": self.title,
            "severity": self.severity,
            "anatomy_id": str(self.anatomy_id) if self.anatomy_id else None,
            "metadata": self.metadata_,
        }


class ClinicalEvent(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    __tablename__ = "clinical_events"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_event_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(
        Enum(ClinicalEventStatus), default=ClinicalEventStatus.ACTIVE, nullable=False
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    onset_date = Column(DateTime(timezone=True), nullable=True, index=True)
    resolved_date = Column(DateTime(timezone=True), nullable=True, index=True)

    # occurrences: JSONB list of events, e.g. [{"date": "2024-03-20", "intensity": 8, "notes": "..."}]
    occurrences = Column(JSONB, nullable=True, default=list)

    # event_metadata: JSONB for specific event data like pregnancy LMP, EDD
    event_metadata = Column(JSONB, nullable=True, default=dict)

    # FHIR / Standard coding
    coding_system = Column(Enum(CodingSystem), nullable=True)
    code = Column(String(100), nullable=True)

    # Integration provenance / dedup (workstream B.1). When both fields are
    # set, a partial unique index (``uq_clinical_event_integration_dedup``)
    # enforces ``(tenant_id, patient_id, source_integration_id, external_id)``
    # uniqueness so re-pulling the same upstream event across syncs doesn't
    # create duplicates. UI-created events leave both NULL and bypass the
    # index. Mirrors ``examinations.source_integration_id`` + ``external_id``.
    source_integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_id = Column(String, nullable=True, index=True)

    # Relationships
    patient = relationship("Patient", backref="clinical_events")
    type_entity = relationship("ClinicalEventType", back_populates="events")
    examination_links = relationship(
        "EventExaminationLink", back_populates="event", cascade="all, delete-orphan"
    )
    observation_links = relationship(
        "EventObservationLink", back_populates="event", cascade="all, delete-orphan"
    )
    occurrence_links = relationship(
        "ClinicalEventOccurrence",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="ClinicalEventOccurrence.occurred_at.desc()",
    )
    anatomy_links = relationship(
        "EventAnatomyLink",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_clinical_event_patient_type", "patient_id", "type_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "type_id": str(self.type_id) if self.type_id else None,
            "type_details": self.type_entity.to_dict() if self.type_entity else None,
            "status": self.status.value,
            "title": self.title,
            "description": self.description,
            "onset_date": self.onset_date.isoformat() if self.onset_date else None,
            "resolved_date": self.resolved_date.isoformat()
            if self.resolved_date
            else None,
            "occurrences": self._serialize_occurrences(),
            "event_metadata": self.event_metadata,
            # Phase 4: explicit rendering hint resolved from the type blueprint.
            # Phase 8a: required (NOT NULL on the type), so this is always set
            # for production rows. The defensive `or ScheduleKind.STATE.value`
            # fallback covers partially-loaded ORM rows where the
            # `type_entity` relationship didn't eager-load (e.g. test mocks) —
            # matches the column's `default=STATE` and keeps the response schema
            # (which now requires schedule_kind) from 500-ing on edge cases.
            "schedule_kind": (
                self.type_entity.schedule_kind.value
                if (self.type_entity and self.type_entity.schedule_kind)
                else ScheduleKind.STATE.value
            ),
            "coding_system": self.coding_system.value if self.coding_system else None,
            "code": self.code,
            # Workstream B.1: integration provenance / dedup fields.
            "source_integration_id": (
                str(self.source_integration_id) if self.source_integration_id else None
            ),
            "external_id": self.external_id,
            "examinations": [
                {
                    "id": str(link.examination.id),
                    "examination_date": link.examination.examination_date.isoformat()
                    if link.examination.examination_date
                    else None,
                    "notes": link.examination.notes,
                    "reason": link.reason,
                    "examination_id": str(link.examination_id),
                }
                for link in self.examination_links
                if link.examination
            ],
            "observations": [
                {
                    **link.observation.to_dict(),
                    "notes": link.notes,
                    "observation_id": str(link.observation_id),
                }
                for link in self.observation_links
                if link.observation
            ],
            "anatomy_links": self._serialize_anatomy_links(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def _serialize_anatomy_links(self) -> list:
        """Serialize anatomy links when the relationship is loaded; ``[]``
        otherwise. Avoids triggering a lazy load during sync serialization."""
        if "anatomy_links" not in self.__dict__:
            return []
        return [
            {
                "id": str(link.id) if link.id else None,
                "anatomy_id": str(link.anatomy_id),
                "name": link.anatomy.name if link.anatomy else None,
                "relation_type": link.relation_type,
            }
            for link in self.anatomy_links
        ]

    def _serialize_occurrences(self) -> list:
        """Source ``occurrences`` from the ``ClinicalEventOccurrence`` table
        when the relationship is loaded and non-empty; otherwise fall back to
        the legacy JSONB column.

        After the Phase-3a backfill migration, every legacy JSONB entry has a
        corresponding model row, so the model is the source of truth and the
        JSONB is a stale duplicate we ignore. For rows written through the
        legacy ``occurrences`` JSONB payload after the migration (no model rows
        yet), the empty model list triggers the JSONB fallback so nothing is
        silently dropped. The ``__dict__`` check avoids triggering a lazy load
        during sync serialization paths (e.g. the FHIR facade's bulk search).
        """
        if "occurrence_links" in self.__dict__:
            model_occs = [o.to_summary() for o in self.occurrence_links]
            if model_occs:
                return model_occs
        return self.occurrences or []

    def to_fhir_dict(self) -> dict:
        """Project this ClinicalEvent to a FHIR R4B Condition resource.

        Maps the metadata-driven event to a FHIR Condition:
        - ``status`` enum → Condition.clinicalStatus (HL7 condition-clinical)
        - ``code`` + ``coding_system`` + ``title`` → Condition.code
        - ``patient_id`` → Condition.subject
        - ``onset_date`` → Condition.onsetDateTime
        - ``resolved_date`` → Condition.abatementDateTime
        - ``description`` → Condition.note
        - ``created_at`` → Condition.recordedDate

        Validated by ``fhir.resources`` via ``build_fhir_resource`` so invalid
        data can never be persisted through the FHIR facade.

        Audit items C7 + C16: Clinical Events gain a FHIR projection so the
        facade can expose them at ``/fhir/R4/Condition`` without a new table.
        """
        # Condition.clinicalStatus uses HL7 condition-clinical codes:
        # active | recurrence | relapse | inactive | resolved | remission
        status_value = (self.status.value if self.status else "UNKNOWN").lower()
        clinical_map = {
            "active": "active",
            "resolved": "resolved",
            "on_hold": "active",  # ON_HOLD has no direct equivalent; active is closest
            "unknown": "active",  # Condition requires clinicalStatus in practice
        }
        clinical_code = clinical_map.get(status_value, "active")
        clinical_status = {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": clinical_code,
                }
            ]
        }

        # Condition.code is required. Build from code/coding_system, with
        # title as fallback text.
        condition_code: dict = {"text": self.title}
        if self.code:
            system_url = self.coding_system.fhir_system if self.coding_system else None
            coding = [{"code": self.code}]
            if system_url:
                coding[0]["system"] = system_url
            condition_code["coding"] = coding

        # Abatement: only set if resolved (otherwise Condition is still active).
        abatement_datetime = None
        if self.resolved_date:
            abatement_datetime = fhir_isoformat(self.resolved_date)

        data = {
            "resourceType": "Condition",
            "id": str(self.id) if self.id else None,
            "clinicalStatus": clinical_status,
            "code": condition_code,
            "subject": {"reference": f"Patient/{self.patient_id}"}
            if self.patient_id
            else None,
            "onsetDateTime": fhir_isoformat(self.onset_date)
            if self.onset_date
            else None,
            "abatementDateTime": abatement_datetime,
            "recordedDate": fhir_isoformat(self.created_at)
            if self.created_at
            else None,
            "note": [{"text": self.description}] if self.description else None,
            "meta": build_meta(version_id=str(self.version or 1)),
        }
        return build_fhir_resource("Condition", data)

    def to_fhir_episode_of_care_dict(self) -> dict:
        """Project this ClinicalEvent to a FHIR R4B ``EpisodeOfCare`` resource.

        A Health-Assistant "health journey" (a 9-month pregnancy, a 2-year
        dental alignment) is semantically a FHIR EpisodeOfCare — *"managing a
        condition over time, including when the patient is not actively being
        seen"* — not just a Condition (which captures only the problem). The
        same row therefore projects to BOTH:

        - ``Condition`` (the problem) via :meth:`to_fhir_dict`,
        - ``EpisodeOfCare`` (the journey) via this method.

        The two projections share the same ``id`` (legal — FHIR ids are
        per-resource-type) and the EpisodeOfCare's ``diagnosis[0].condition``
        references ``Condition/{id}`` so a client can walk between them. No new
        table, no dual-write — same hybrid-storage pattern as Condition.

        Maps:
        - ``status`` enum → EpisodeOfCare.status
          (active→active, resolved→finished, on_hold→onhold, unknown→planned)
        - ``patient_id`` → EpisodeOfCare.patient
        - ``onset_date``/``resolved_date`` → EpisodeOfCare.period.start/end
        - ``type_entity.name`` → EpisodeOfCare.type[0].text (the journey template)
        - self → EpisodeOfCare.diagnosis[0].condition (Condition/{id})

        Validated by ``fhir.resources`` via ``build_fhir_resource``.
        """
        status_name = self.status.name if self.status else "UNKNOWN"
        eoc_status_map = {
            "ACTIVE": "active",
            "RESOLVED": "finished",
            "ON_HOLD": "onhold",
            "UNKNOWN": "planned",
        }
        eoc_status = eoc_status_map.get(status_name, "active")

        period: dict = {}
        if self.onset_date:
            period["start"] = fhir_isoformat(self.onset_date)
        if self.resolved_date:
            period["end"] = fhir_isoformat(self.resolved_date)

        type_list = None
        # Guard against triggering a lazy load during sync serialization (the
        # facade search path loads rows generically without eager-loading
        # type_entity). ``type`` is 0..* optional in FHIR, so omitting it when
        # the relationship isn't loaded is safe.
        if (
            "type_entity" in self.__dict__
            and self.type_entity
            and self.type_entity.name
        ):
            type_list = [{"text": self.type_entity.name}]

        # The Condition this episode manages is this same row's Condition
        # projection (same id, different resource type).
        diagnosis = [
            {
                "condition": {"reference": f"Condition/{self.id}"},
                "rank": 1,
            }
        ]

        data = {
            "resourceType": "EpisodeOfCare",
            "id": str(self.id) if self.id else None,
            "status": eoc_status,
            "patient": {"reference": f"Patient/{self.patient_id}"}
            if self.patient_id
            else None,
            "period": period or None,
            "type": type_list,
            "diagnosis": diagnosis,
            "meta": build_meta(version_id=str(self.version or 1)),
        }
        return build_fhir_resource("EpisodeOfCare", data)


class EventAnatomyLink(Base, UUIDMixin, TimestampMixin):
    """Typed link between a ClinicalEvent and one or more anatomy sites.

    Carries a ``relation_type`` (e.g. ``primary_site``, ``radiates_to``,
    ``referred_to``) so a journey can record where a symptom originates and
    where it radiates. The (event_id, anatomy_id) pair is unique. Previously
    this table existed but was unwired (anatomy was tracked ad-hoc in
    ``event_metadata.body_part_id`` JSONB); Phase 3b promotes it to the
    structured path.
    """

    __tablename__ = "event_anatomy_links"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    anatomy_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("anatomy_structures.id", ondelete="CASCADE"),
        nullable=False,
    )

    # E.g., 'primary_site', 'radiates_to'
    relation_type = Column(String(50), nullable=True)

    # Relationships
    event = relationship("ClinicalEvent", back_populates="anatomy_links")
    anatomy = relationship("AnatomyStructure", lazy="selectin")

    __table_args__ = (
        Index("idx_event_anatomy_link", "event_id", "anatomy_id", unique=True),
    )

"""Notification target resolution.

A notification is emitted with a list of *target specs* — high-level
principal descriptions such as "PATIENT <id>", "DOCTOR <id>", "TENANT",
or "SYSTEM". Delivery, however, is always to a concrete login account
(``users.id``). This module expands target specs into the set of user ids
that should receive a notification.

Resolution rules
----------------
* ``USER <id>``           → ``{id}``
* ``PATIENT <id>``        → the patient's own ``user_id`` (if linked) plus
  every doctor who has examined that patient (via ``examination_doctors``).
* ``DOCTOR <id>``         → the doctor's ``user_id`` (if linked).
* ``TENANT <id>``         → every user in that tenant.
* ``SYSTEM``              → every ``SYSTEM_ADMIN`` user (cross-tenant
  broadcast recipients). Caller may instead pass an explicit tenant id to
  restrict.

Unlinked clinical records (``user_id IS NULL``) are silently dropped — a
notification cannot be delivered to a record with no login account. This
matches the platform's "identity linking is optional" model.
"""
from __future__ import annotations

import logging
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RecipientKind, Role
from app.models.user_model import UserModel
from app.models.doctor_model import DoctorModel
from app.models.fhir.patient import Patient
from app.models.examination_model import ExaminationModel
from app.models.associations import examination_doctors

logger = logging.getLogger(__name__)


class TargetSpec(dict):
    """Typed dict wrapper for a target spec.

    Shape: ``{"kind": "USER|PATIENT|DOCTOR|TENANT|SYSTEM", "id": "<uuid>"}``
    (``id`` omitted for ``TENANT``/``SYSTEM`` when scoped to the caller).
    """

    __slots__ = ()


def _uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


async def resolve_targets(
    session: AsyncSession,
    tenant_id: str | UUID | None,
    target_specs: Iterable[dict],
) -> set[UUID]:
    """Expand target specs into a deduplicated set of ``user_id``s.

    Parameters
    ----------
    session:
        A live async session used for the membership lookups.
    tenant_id:
        The tenant scope. Used as a fallback when a target spec is
        ``TENANT`` without an explicit id, and as a hard isolation
        boundary (a PATIENT/DOCTOR id from another tenant resolves to
        nothing).
    target_specs:
        Iterable of ``{"kind", "id"}`` dicts.
    """
    resolved: set[UUID] = set()
    tenant_uuid = _uuid(tenant_id)

    for spec in target_specs or []:
        if not isinstance(spec, dict):
            continue
        kind = spec.get("kind")
        spec_id = _uuid(spec.get("id"))

        if kind == RecipientKind.USER.value:
            if spec_id is not None:
                resolved.add(spec_id)

        elif kind == RecipientKind.PATIENT.value:
            if spec_id is None:
                continue
            resolved |= await _resolve_patient(session, tenant_uuid, spec_id)

        elif kind == RecipientKind.DOCTOR.value:
            if spec_id is None:
                continue
            resolved |= await _resolve_doctor(session, tenant_uuid, spec_id)

        elif kind == RecipientKind.TENANT.value:
            scope = spec_id or tenant_uuid
            if scope is None:
                continue
            resolved |= await _resolve_tenant(session, scope)

        elif kind == RecipientKind.SYSTEM.value:
            resolved |= await _resolve_system_admins(session)

        else:
            logger.warning("Unknown notification target kind: %s", kind)

    resolved.discard(None)
    return resolved


async def _resolve_patient(
    session: AsyncSession, tenant_id: UUID | None, patient_id: UUID
) -> set[UUID]:
    users: set[UUID] = set()
    stmt = select(Patient.user_id).where(Patient.id == patient_id)
    if tenant_id is not None:
        stmt = stmt.where(Patient.tenant_id == tenant_id)
    row = (await session.execute(stmt)).first()
    if row and row[0]:
        users.add(row[0])

    # Doctors who have examined this patient (care team).
    doc_stmt = (
        select(DoctorModel.user_id)
        .join(examination_doctors, examination_doctors.c.doctor_id == DoctorModel.id)
        .join(
            ExaminationModel,
            ExaminationModel.id == examination_doctors.c.examination_id,
        )
        .where(ExaminationModel.patient_id == patient_id)
    )
    if tenant_id is not None:
        doc_stmt = doc_stmt.where(DoctorModel.tenant_id == tenant_id)
    for (uid,) in (await session.execute(doc_stmt)).all():
        if uid:
            users.add(uid)
    return users


async def _resolve_doctor(
    session: AsyncSession, tenant_id: UUID | None, doctor_id: UUID
) -> set[UUID]:
    stmt = select(DoctorModel.user_id).where(DoctorModel.id == doctor_id)
    if tenant_id is not None:
        stmt = stmt.where(DoctorModel.tenant_id == tenant_id)
    row = (await session.execute(stmt)).first()
    if row and row[0]:
        return {row[0]}
    return set()


async def _resolve_tenant(session: AsyncSession, tenant_id: UUID) -> set[UUID]:
    stmt = select(UserModel.id).where(UserModel.tenant_id == tenant_id)
    return {row[0] for row in (await session.execute(stmt)).all() if row[0]}


async def _resolve_system_admins(session: AsyncSession) -> set[UUID]:
    stmt = select(UserModel.id).where(UserModel.role == Role.SYSTEM_ADMIN.value)
    return {row[0] for row in (await session.execute(stmt)).all() if row[0]}

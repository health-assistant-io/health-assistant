from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import is_scoped_principal
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import ErasureSubjectType, Role
from app.models.patient_access_grant import GrantPermission
from app.models.user_model import UserModel
from app.schemas.user import TokenData
from app.services.audit.audit_service import audit_read, log_audit_action
from app.services.auth.access import accessible_patient_ids, check_patient_access
from app.services.data_lifecycle.erasure import ErasureError, run_erasure
from app.services.fhir.fhir_service import (
    create_patient,
    list_patients,
    update_patient,
    update_patient_layout,
)

router = APIRouter(prefix="/patients", tags=["patients"])


async def _validate_patient_user_id(
    patient_data: dict, current_user: TokenData, db: AsyncSession
) -> None:
    """Keep patient ownership assignment inside the caller's authority.

    A USER may never re-assign a patient (it is already theirs); ADMIN /
    MANAGER may assign only an existing user of the same tenant. A dangling
    or foreign-tenant id would otherwise grant that user access to the
    record (or surface an unhandled FK error).
    """
    if current_user.role == Role.USER.value:
        patient_data["user_id"] = str(current_user.user_id)
        return
    if "user_id" not in patient_data:
        return

    raw = patient_data.get("user_id")
    if not raw:
        patient_data["user_id"] = None
        return

    try:
        from uuid import UUID

        target_id = UUID(str(raw))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid user id") from None

    result = await db.execute(
        select(UserModel.id).where(
            UserModel.id == target_id,
            UserModel.tenant_id == current_user.tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found in this tenant")
    patient_data["user_id"] = str(target_id)


@router.get("")
async def list_patients_endpoint(
    tenant_id: str = Query(None),
    user_id: str = Query(None),
    limit: int = Query(10),
    offset: int = Query(0),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List patients (with pagination and optional user_id filter).

    Grant-aware (ADR 0054 Phase 2): a USER (and a MANAGER under
    ``manager_scope=assigned``) sees their own patients + the patients an
    active grant reaches — not just ``Patient.user_id``.
    """
    final_tenant_id = current_user.tenant_id

    reachable = await accessible_patient_ids(current_user, db)
    if (
        reachable is None
        and not is_scoped_principal(current_user)
        and current_user.role == Role.SYSTEM_ADMIN.value
    ):
        if tenant_id:
            final_tenant_id = tenant_id
        elif user_id:
            final_tenant_id = None

    if reachable is not None:
        patients = await list_patients(final_tenant_id, limit, offset, patient_ids=reachable)
    else:
        patients = await list_patients(final_tenant_id, limit, offset, user_id=user_id)
    return patients


@router.post("")
async def create_patient_endpoint(
    patient_data: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new patient"""
    await _validate_patient_user_id(patient_data, current_user, db)

    patient = await create_patient(patient_data, current_user.tenant_id)
    # §17: patient (record) writes are audited alongside the clinical
    # resources. The diff stores the *persisted* model snapshot (S5), not
    # the raw request dict — unknown request keys must not be able to
    # carry PHI past the redaction map.
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="create_patient",
        resource_type="Patient",
        resource_id=getattr(patient, "id", None),
        new_value=patient.to_dict() if hasattr(patient, "to_dict") else None,
    )
    return patient


@router.get("/{patient_id}")
@audit_read("Patient", id_param="patient_id")
async def get_patient_endpoint(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get patient by ID"""
    return await check_patient_access(patient_id, current_user, db)


@router.put("/{patient_id}/layout")
async def update_patient_layout_endpoint(
    patient_id: str,
    layout: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update patient dashboard layout (the single-layout slot on Patient.dashboard_layout).

    Distinct from the per-user multi-layout routes at
    ``/patients/{patient_id}/layouts/*`` (``patient_layout.py``).
    """
    await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)
    patient = await update_patient_layout(patient_id, layout)
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="update_patient_layout",
        resource_type="Patient",
        resource_id=patient_id,
        # S5: dashboard_layout is schema-less JSONB — copying its values
        # into the audit would let arbitrary request payloads bypass the
        # redaction map. The diff carries the persisted shape (its keys)
        # only; the full layout stays on the patient record itself.
        new_value={
            "dashboard_layout_keys": sorted(
                (getattr(patient, "dashboard_layout", None) or {}).keys()
            )
        },
    )
    return patient


@router.put("/{patient_id}")
async def update_patient_endpoint(
    patient_id: str,
    patient_data: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update patient information"""
    await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)
    await _validate_patient_user_id(patient_data, current_user, db)
    patient = await update_patient(patient_id, patient_data)
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="update_patient",
        resource_type="Patient",
        resource_id=patient_id,
        # S5: the persisted model snapshot, never the raw request dict.
        new_value=patient.to_dict() if hasattr(patient, "to_dict") else None,
    )
    return patient


@router.delete("/{patient_id}")
async def delete_patient_endpoint(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Erase a patient record and every owned row (data-subject erasure).

    This is not a facade ``DELETE`` — the erasure walker hard-deletes the
    record in children-before-parents order, removes files/telemetry/secrets,
    and writes the audit ledger row. A dry-run impact report is available at
    ``POST /data-lifecycle/erasures`` with ``dry_run=true``.
    """
    patient = await check_patient_access(
        patient_id, current_user, db, required=GrantPermission.WRITE
    )

    if current_user.role not in [Role.SYSTEM_ADMIN.value, Role.ADMIN.value]:  # noqa: SIM102 -- nested conditional; merge when touched
        if str(patient.user_id) != str(current_user.user_id):
            raise HTTPException(status_code=403, detail="Access denied")

    prior_snapshot = patient.to_dict() if hasattr(patient, "to_dict") else None
    try:
        request = await run_erasure(
            db,
            subject_type=ErasureSubjectType.PATIENT,
            subject_id=UUID(patient_id),
            requested_by=current_user.user_id,
            dry_run=False,
        )
    except ErasureError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="delete_patient",
        resource_type="Patient",
        resource_id=patient_id,
        old_value=prior_snapshot,  # gate-allow: *, audit diff
    )
    return {"message": "Patient deleted successfully", "erasure_id": str(request.id)}

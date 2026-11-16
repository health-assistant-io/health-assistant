"""Data-lifecycle API: erasure requests, the erasure ledger, retention policy.

Erasure is always available as a dry run first (the impact report), then
executed with ``dry_run=false``. Patient/user deletions are also wired from
the existing ``DELETE /patients/{id}`` / ``DELETE /users/{id}`` endpoints to
this same walker — one cascade implementation, one report shape.

Retention policies are instance-level operator data: any authenticated admin
may read them, only a ``SYSTEM_ADMIN`` may edit (the policy table is global,
not tenant-scoped). Editing a row changes the next job's cutoff immediately.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    RoleChecker,
    TokenData,
    get_current_user,
    verify_password,
)
from app.models.data_lifecycle_model import ErasureRequest, RetentionPolicy
from app.models.enums import ErasureSubjectType, RetentionAction, Role
from app.schemas.data_lifecycle import (
    ErasureListResponse,
    ErasureRequestCreate,
    ErasureResponse,
    RetentionPolicyResponse,
    RetentionPolicyUpdate,
    RetentionRunResponse,
)
from app.services.data_lifecycle.erasure import (
    ErasureError,
    ErasureForbidden,
    assert_can_erase,
    replay_erasure_ledger,
    resolve_subject,
    run_erasure,
)
from app.services.data_lifecycle.retention_service import run_retention
from app.services.tenancy.user_service import (
    get_user_by_id,
    reset_login_failures,
    set_login_failures,
)

router = APIRouter(prefix="/data-lifecycle", tags=["data-lifecycle"])


def _typed_error(exc: ErasureError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.detail},
    )


def _subject_tenant_scope(current_user: TokenData) -> UUID | None:
    """SYSTEM_ADMIN reaches every tenant; everyone else is token-scoped."""
    if current_user.role == Role.SYSTEM_ADMIN.value:
        return None
    return current_user.tenant_id


@router.post(
    "/erasures",
    response_model=ErasureResponse,
    responses={
        400: {"description": "unknown subject type"},
        403: {"description": "not allowed to erase this subject"},
        404: {"description": "subject not found in scope"},
    },
)
async def create_erasure(
    payload: ErasureRequestCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ErasureResponse:
    """Create (and run) an erasure request; ``dry_run`` returns the report only."""
    try:
        subject = await resolve_subject(
            db,
            payload.subject_type,
            payload.subject_id,
            tenant_id=_subject_tenant_scope(current_user),
        )
        assert_can_erase(current_user, payload.subject_type, subject)

        # A self-erasure is account destruction: the same password gate as
        # DELETE /users/me (side-stepping it here would revert that fix).
        if (
            not payload.dry_run
            and payload.subject_type == ErasureSubjectType.USER
            and str(payload.subject_id) == str(current_user.user_id)
        ):
            if not payload.password:
                raise ErasureForbidden("Password confirmation is required for self-erasure.")
            user = await get_user_by_id(current_user.user_id)
            if user is None or not verify_password(payload.password, user.password_hash or ""):
                if user is not None:
                    failed = int(user.failed_login_attempts or 0) + 1
                    locked = user.locked_until
                    if failed >= settings.HA_AUTH_LOCKOUT_THRESHOLD:
                        locked = datetime.now(UTC) + timedelta(
                            minutes=settings.HA_AUTH_LOCKOUT_MINUTES
                        )
                    await set_login_failures(user.id, failed, locked)
                raise ErasureForbidden("Current password is incorrect.")
            await reset_login_failures(user.id)

            if user.role == Role.SYSTEM_ADMIN.value:
                from app.models.user_model import UserModel

                remaining = await db.execute(
                    select(func.count(UserModel.id)).where(
                        UserModel.role == Role.SYSTEM_ADMIN.value,
                        UserModel.is_active.is_(True),
                        UserModel.id != user.id,
                    )
                )
                if int(remaining.scalar_one() or 0) == 0:
                    raise ErasureForbidden("The last SYSTEM_ADMIN cannot erase their own account.")

        request = await run_erasure(
            db,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            requested_by=current_user.user_id if isinstance(current_user.user_id, UUID) else None,
            dry_run=payload.dry_run,
            export_before_erase=payload.export_before_erase,
        )
    except ErasureError as exc:
        raise _typed_error(exc) from exc
    return ErasureResponse(**request.to_dict())


@router.get("/erasures", response_model=ErasureListResponse)
async def list_erasures(
    limit: int = Query(50, ge=1, le=200),
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> ErasureListResponse:
    """List erasure requests in the caller's tenant (SYSTEM_ADMIN: all)."""
    query = select(ErasureRequest).order_by(ErasureRequest.created_at.desc()).limit(limit)
    if current_user.role != Role.SYSTEM_ADMIN.value:
        query = query.where(ErasureRequest.tenant_id == current_user.tenant_id)
    rows = list((await db.execute(query)).scalars().all())
    return ErasureListResponse(
        requests=[ErasureResponse(**row.to_dict()) for row in rows], total=len(rows)
    )


@router.get("/erasures/{request_id}", response_model=ErasureResponse)
async def get_erasure(
    request_id: UUID,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> ErasureResponse:
    """Read one erasure request and its impact report."""
    row = (
        await db.execute(select(ErasureRequest).where(ErasureRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "erasure_request_not_found", "message": "Erasure request not found"},
        )
    if current_user.role != Role.SYSTEM_ADMIN.value and str(row.tenant_id) != str(
        current_user.tenant_id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "erasure_request_not_found", "message": "Erasure request not found"},
        )
    return ErasureResponse(**row.to_dict())


@router.post("/ledger/replay")
async def replay_ledger(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-apply every done erasure (the restore-time hook, idempotent)."""
    return await replay_erasure_ledger(db)


@router.get("/retention-policies", response_model=list[RetentionPolicyResponse])
async def list_retention_policies(
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> list[RetentionPolicyResponse]:
    """Read the instance's retention policy rows (the jobs' only input)."""
    rows = (
        await db.execute(
            select(RetentionPolicy).order_by(RetentionPolicy.scope, RetentionPolicy.scope_value)
        )
    ).scalars()
    return [RetentionPolicyResponse(**row.to_dict()) for row in rows]


@router.patch(
    "/retention-policies/{policy_id}",
    response_model=RetentionPolicyResponse,
)
async def update_retention_policy(
    policy_id: UUID,
    payload: RetentionPolicyUpdate,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> RetentionPolicyResponse:
    """Operator edit — the next job run uses the new cutoff."""
    row = (
        await db.execute(select(RetentionPolicy).where(RetentionPolicy.id == policy_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "retention_policy_not_found", "message": "Policy not found"},
        )
    if payload.days is not None:
        row.days = payload.days
    if payload.action is not None:
        row.action = RetentionAction(payload.action)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    await db.commit()
    await db.refresh(row)
    return RetentionPolicyResponse(**row.to_dict())


@router.post("/retention/run", response_model=RetentionRunResponse)
async def run_retention_now(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> RetentionRunResponse:
    """Run the retention job synchronously (the daily beat does this too)."""
    result = await run_retention(db)
    return RetentionRunResponse(**result)

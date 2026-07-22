"""System-admin tenant management service.

Encapsulates every operation the ``/admin/tenants`` surface performs:

  * Listing + detail (with usage stats) of all tenants.
  * Create / update / deactivate / reactivate / hard-delete.
  * ``switch_into_tenant`` — mints a scoped JWT so a SYSTEM_ADMIN can
    operate inside another tenant while keeping their real identity in
    ``original_tenant_id`` / ``original_user_id`` claims.
  * Per-tenant user management: list, role change, active toggle, invite.
  * Audit-log viewer scoped to a tenant.

The class is instantiated per-request with the request's ``AsyncSession``
(the preferred style for new complex services — see the backend service
conventions). Mutating methods persist an ``AuditLog`` entry via
``audit_service.log_audit_action`` so every administrative action is
traceable.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_invite_token,
)
from app.models.audit_model import AuditLog
from app.models.document_model import DocumentModel
from app.models.enums import Role
from app.models.examination_model import ExaminationModel
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import Observation, Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.schemas.tenant import (
    SwitchTenantResponse,
    TenantCreate,
    TenantDetailResponse,
    TenantResponse,
    TenantStats,
    TenantUpdate,
    UpdateTenantUser,
    UserSummary,
)
from app.services.audit_service import log_audit_action
from app.utils.slug import slugify

logger = logging.getLogger(__name__)


class TenantAdminService:
    """System-admin tenant management.

    Every method assumes the caller has already passed the
    ``RoleChecker([Role.SYSTEM_ADMIN])`` gate at the endpoint layer; the
    ``actor`` parameter is used purely for audit provenance.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_uuid(value: str | UUID, field: str = "id") -> UUID:
        """Coerce ``value`` to UUID or raise a 400 (audit B16 pattern)."""
        if isinstance(value, UUID):
            return value
        try:
            return UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field}: must be a valid UUID.",
            )

    async def _get_tenant_or_404(self, tenant_id: UUID) -> TenantModel:
        result = await self.db.execute(
            select(TenantModel).where(TenantModel.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found.",
            )
        return tenant

    async def _ensure_unique_slug(
        self, slug: str, *, exclude_id: Optional[UUID] = None
    ) -> str:
        """Return a slug guaranteed unique within the tenants table.

        On collision we append a short random suffix and try again. We
        cap iterations at 8 to avoid an unbounded loop in pathological
        cases (should never happen in practice).
        """
        candidate = slug
        for _ in range(8):
            stmt = (
                select(func.count())
                .select_from(TenantModel)
                .where(TenantModel.slug == candidate)
            )
            if exclude_id is not None:
                stmt = stmt.where(TenantModel.id != exclude_id)
            count = (await self.db.execute(stmt)).scalar() or 0
            if count == 0:
                return candidate
            candidate = f"{slug}-{secrets.token_hex(2)}"
        # Last resort — full random suffix.
        return f"{slug}-{secrets.token_hex(4)}"

    # ------------------------------------------------------------------
    # List / detail / stats
    # ------------------------------------------------------------------

    async def list_tenants(
        self,
        *,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[TenantModel], int]:
        limit = max(1, min(limit, 250))
        offset = max(0, offset)

        query = select(TenantModel)
        count_query = select(func.count()).select_from(TenantModel)

        if search:
            like = f"%{search.lower()}%"
            cond = func.lower(TenantModel.name).like(like) | func.lower(
                TenantModel.slug
            ).like(like)
            query = query.where(cond)
            count_query = count_query.where(cond)
        if is_active is not None:
            query = query.where(TenantModel.is_active == is_active)
            count_query = count_query.where(TenantModel.is_active == is_active)

        total = (await self.db.execute(count_query)).scalar() or 0
        query = (
            query.order_by(TenantModel.created_at.desc()).limit(limit).offset(offset)
        )
        items = (await self.db.execute(query)).scalars().all()
        return list(items), int(total)

    async def _count(self, model, tenant_id: UUID) -> int:
        stmt = (
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )
        return int((await self.db.execute(stmt)).scalar() or 0)

    async def _compute_stats(self, tenant_id: UUID) -> TenantStats:
        (
            users_count,
            active_users_count,
            patients_count,
            orgs_count,
            exams_count,
            obs_count,
            docs_count,
        ) = await asyncio.gather(
            self._count(UserModel, tenant_id),
            self.db.execute(
                select(func.count())
                .select_from(UserModel)
                .where(
                    UserModel.tenant_id == tenant_id,
                    UserModel.is_active.is_(True),
                )
            ),
            self._count(Patient, tenant_id),
            self._count(OrganizationModel, tenant_id),
            self._count(ExaminationModel, tenant_id),
            self._count(Observation, tenant_id),
            self._count(DocumentModel, tenant_id),
        )
        return TenantStats(
            users_count=users_count,
            active_users_count=int(active_users_count.scalar() or 0),
            patients_count=patients_count,
            organizations_count=orgs_count,
            examinations_count=exams_count,
            observations_count=obs_count,
            documents_count=docs_count,
            storage_bytes=0,
        )

    async def get_tenant_detail(self, tenant_id: UUID) -> TenantDetailResponse:
        tenant = await self._get_tenant_or_404(tenant_id)
        stats, owner = await asyncio.gather(
            self._compute_stats(tenant.id),
            self._load_owner(tenant.owner_id),
        )
        return TenantDetailResponse(
            **TenantResponse.model_validate(tenant).model_dump(),
            stats=stats,
            owner=UserSummary.model_validate(owner) if owner else None,
        )

    async def _load_owner(self, owner_id: Optional[UUID]) -> Optional[UserModel]:
        if owner_id is None:
            return None
        result = await self.db.execute(
            select(UserModel).where(UserModel.id == owner_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create_tenant(self, payload: TenantCreate, actor_id: UUID) -> TenantModel:
        slug = await self._ensure_unique_slug(slugify(payload.slug or payload.name))
        tenant = TenantModel(
            name=payload.name.strip(),
            slug=slug,
            description=payload.description,
            is_active=True,
            owner_id=payload.owner_id or actor_id,
            settings=payload.settings or {},
            created_by=actor_id,
            updated_by=actor_id,
        )
        self.db.add(tenant)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            logger.warning("Tenant create integrity error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A tenant with that slug already exists.",
            )
        await self.db.commit()
        await self.db.refresh(tenant)

        await log_audit_action(
            tenant_id=tenant.id,
            user_id=actor_id,
            action="tenant.create",
            resource_type="tenant",
            resource_id=tenant.id,
            new_value=tenant.to_dict(),
        )
        return tenant

    async def update_tenant(
        self,
        tenant_id: UUID,
        payload: TenantUpdate,
        actor_id: UUID,
    ) -> TenantModel:
        tenant = await self._get_tenant_or_404(tenant_id)
        old_snapshot = tenant.to_dict()

        if payload.name is not None:
            tenant.name = payload.name.strip()
        if payload.description is not None:
            tenant.description = payload.description
        if payload.settings is not None:
            tenant.settings = payload.settings
            flag_modified(tenant, "settings")
        if payload.slug is not None and payload.slug != tenant.slug:
            candidate = await self._ensure_unique_slug(
                slugify(payload.slug), exclude_id=tenant.id
            )
            tenant.slug = candidate

        tenant.updated_by = actor_id
        await self.db.commit()
        await self.db.refresh(tenant)

        await log_audit_action(
            tenant_id=tenant.id,
            user_id=actor_id,
            action="tenant.update",
            resource_type="tenant",
            resource_id=tenant.id,
            old_value=old_snapshot,
            new_value=tenant.to_dict(),
        )
        return tenant

    async def set_active(
        self, tenant_id: UUID, active: bool, actor_id: UUID
    ) -> TenantModel:
        tenant = await self._get_tenant_or_404(tenant_id)
        old_value = tenant.is_active
        if tenant.is_active == active:
            return tenant
        tenant.is_active = active
        tenant.updated_by = actor_id
        await self.db.commit()
        await self.db.refresh(tenant)
        await log_audit_action(
            tenant_id=tenant.id,
            user_id=actor_id,
            action="tenant.deactivate" if not active else "tenant.reactivate",
            resource_type="tenant",
            resource_id=tenant.id,
            old_value={"is_active": old_value},
            new_value={"is_active": active},
        )
        return tenant

    async def hard_delete_tenant(
        self,
        tenant_id: UUID,
        confirm_name: str,
        actor_id: UUID,
    ) -> None:
        tenant = await self._get_tenant_or_404(tenant_id)
        if confirm_name != tenant.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Confirmation name does not match the tenant name.",
            )
        snapshot = tenant.to_dict()
        await self.db.delete(tenant)
        await self.db.commit()
        await log_audit_action(
            tenant_id=None,
            user_id=actor_id,
            action="tenant.delete",
            resource_type="tenant",
            resource_id=tenant_id,
            old_value=snapshot,
        )

    # ------------------------------------------------------------------
    # Tenant switching (SYSTEM_ADMIN → operate inside another tenant)
    # ------------------------------------------------------------------

    async def switch_into_tenant(
        self, tenant_id: UUID, actor: Any
    ) -> SwitchTenantResponse:
        """Mint a scoped JWT so a SYSTEM_ADMIN can operate inside ``tenant_id``.

        The new token keeps ``role = SYSTEM_ADMIN`` (so the admin can still
        use admin features) but carries ``tenant_id`` of the target and a
        ``original_tenant_id`` / ``original_user_id`` / ``switched = True``
        claim set used by ``switch_back``.
        """
        tenant = await self._get_tenant_or_404(tenant_id)
        if not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot switch into an inactive tenant.",
            )

        access_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
        refresh_expires = timedelta(days=7)
        base_claims = {
            "sub": actor.sub,
            "user_id": str(actor.user_id),
            "role": actor.role,
            "original_tenant_id": str(actor.tenant_id),
            "original_user_id": str(actor.user_id),
            "switched": True,
            "scoped_tenant_id": str(tenant.id),
        }
        access_claims = {**base_claims, "tenant_id": str(tenant.id)}
        refresh_claims = {**base_claims, "tenant_id": str(tenant.id)}

        access_token = create_access_token(access_claims, expires_delta=access_expires)
        refresh_token = create_access_token(
            refresh_claims, expires_delta=refresh_expires
        )

        await log_audit_action(
            tenant_id=tenant.id,
            user_id=actor.user_id,
            action="tenant.switch_into",
            resource_type="tenant",
            resource_id=tenant.id,
        )
        return SwitchTenantResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=int(access_expires.total_seconds()),
            scoped_tenant_id=tenant.id,
            original_tenant_id=actor.tenant_id,
            tenant=TenantResponse.model_validate(tenant),
        )

    async def switch_back(self, actor: Any) -> SwitchTenantResponse:
        """Restore the original SYSTEM_ADMIN session after a switch."""
        if not getattr(actor, "switched", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current session is not a switched session.",
            )

        original_tenant_id = actor.original_tenant_id
        original_user_id = actor.original_user_id
        tenant = await self._get_tenant_or_404(original_tenant_id)

        access_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
        refresh_expires = timedelta(days=7)
        restored_claims = {
            "sub": actor.sub,
            "user_id": str(original_user_id),
            "tenant_id": str(original_tenant_id),
            "role": actor.role,
        }
        access_token = create_access_token(
            restored_claims, expires_delta=access_expires
        )
        refresh_token = create_access_token(
            restored_claims, expires_delta=refresh_expires
        )

        await log_audit_action(
            tenant_id=original_tenant_id,
            user_id=original_user_id,
            action="tenant.switch_back",
            resource_type="tenant",
            resource_id=original_tenant_id,
        )
        return SwitchTenantResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=int(access_expires.total_seconds()),
            scoped_tenant_id=original_tenant_id,
            original_tenant_id=original_tenant_id,
            tenant=TenantResponse.model_validate(tenant),
        )

    # ------------------------------------------------------------------
    # Per-tenant user management
    # ------------------------------------------------------------------

    async def list_tenant_users(
        self,
        tenant_id: UUID,
        *,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[UserModel], int]:
        limit = max(1, min(limit, 250))
        offset = max(0, offset)
        query = select(UserModel).where(UserModel.tenant_id == tenant_id)
        count_query = (
            select(func.count())
            .select_from(UserModel)
            .where(UserModel.tenant_id == tenant_id)
        )
        if search:
            like = f"%{search.lower()}%"
            cond = func.lower(UserModel.email).like(like)
            query = query.where(cond)
            count_query = count_query.where(cond)
        total = (await self.db.execute(count_query)).scalar() or 0
        query = query.order_by(UserModel.created_at.desc()).limit(limit).offset(offset)
        items = (await self.db.execute(query)).scalars().all()
        return list(items), int(total)

    async def update_tenant_user(
        self,
        tenant_id: UUID,
        user_id: UUID,
        payload: UpdateTenantUser,
        actor_id: UUID,
    ) -> UserModel:
        result = await self.db.execute(
            select(UserModel).where(
                UserModel.id == user_id,
                UserModel.tenant_id == tenant_id,
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in this tenant.",
            )

        old_snapshot = {
            "role": user.role.value if user.role else None,
            "is_active": user.is_active,
        }

        if payload.role is not None:
            # SYSTEM_ADMIN is bootstrap-only — never grantable from this surface.
            try:
                new_role = Role(payload.role)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role: {payload.role}.",
                )
            if new_role == Role.SYSTEM_ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="SYSTEM_ADMIN cannot be granted via tenant user management.",
                )
            if user.role == Role.SYSTEM_ADMIN and new_role != Role.SYSTEM_ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote a SYSTEM_ADMIN from this surface.",
                )
            user.role = new_role

        if payload.is_active is not None:
            user.is_active = payload.is_active

        user.updated_by = actor_id
        await self.db.commit()
        await self.db.refresh(user)

        await log_audit_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action="tenant_user.update",
            resource_type="tenant_user",
            resource_id=user.id,
            old_value=old_snapshot,
            new_value={
                "role": user.role.value if user.role else None,
                "is_active": user.is_active,
            },
        )
        return user

    async def mint_invite(
        self,
        tenant_id: UUID,
        email: Optional[str],
        role: str,
        expires_days: int,
    ) -> Dict[str, Any]:
        if role == Role.SYSTEM_ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SYSTEM_ADMIN cannot be granted via invite.",
            )
        tenant = await self._get_tenant_or_404(tenant_id)
        token = create_invite_token(
            tenant_id=str(tenant.id),
            email=email,
            role=role,
            expires_days=expires_days,
        )
        return {
            "invite_token": token,
            "tenant_id": tenant.id,
            "role": role,
            "expires_in_days": expires_days,
        }

    # ------------------------------------------------------------------
    # Audit viewer
    # ------------------------------------------------------------------

    async def list_audit_entries(
        self,
        tenant_id: UUID,
        *,
        action: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[list[AuditLog], int]:
        limit = max(1, min(limit, 250))
        offset = max(0, offset)
        query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        count_query = (
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == tenant_id)
        )
        if action:
            query = query.where(AuditLog.action == action)
            count_query = count_query.where(AuditLog.action == action)
        total = (await self.db.execute(count_query)).scalar() or 0
        query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        items = (await self.db.execute(query)).scalars().all()
        return list(items), int(total)

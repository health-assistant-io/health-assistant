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
conventions). Mutating methods persist an ``AuditEvent`` entry via
``audit_service.log_audit_action`` so every administrative action is
traceable (identity-auth §17).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core import token_store
from app.core.security import (
    AUTH_MODE_DEMO,
    create_invite_token,
)
from app.models.audit_model import AuditEvent
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
from app.services.auth_session_service import issue_session
from app.utils.slug import slugify

logger = logging.getLogger(__name__)


def _refuse_demo_principal(actor: Any) -> None:
    """§13 (plan 16 H7): the ``demo`` principal never mints tenant-scoped
    tokens. Tenant switching is an operator surface; even where the demo
    user holds an admin role, its credential-free session must not carry a
    ``switched`` token into another tenant (the demo stamp itself would
    survive the switch — ``auth_mode`` is preserved on the minted pair —
    so the surface itself stays closed to the demo principal).
    """
    if getattr(actor, "auth_mode", None) == AUTH_MODE_DEMO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo principal cannot switch tenants.",
        )


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

    async def _get_actor_user(self, actor: Any) -> UserModel:
        """The live user row behind a (possibly switched) principal.

        Issue-time claims (ver, role, email, user_id) always come from the
        DB row — never from the old token (identity-auth §8). Uses the
        shared user service (own session) so the row is read even when the
        request session is a narrow unit-test double.
        """
        from app.services.user_service import get_user_by_id

        user = await get_user_by_id(actor.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User no longer exists.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    async def _ensure_unique_slug(
        self, slug: str, *, exclude_id: UUID | None = None
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
        search: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TenantModel], int]:
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

    async def _load_owner(self, owner_id: UUID | None) -> UserModel | None:
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

        §13 (plan 16 H7): a ``demo``-stamped principal is refused — the
        demo session never mints cross-tenant scoped tokens.
        """
        _refuse_demo_principal(actor)
        tenant = await self._get_tenant_or_404(tenant_id)
        if not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot switch into an inactive tenant.",
            )

        user = await self._get_actor_user(actor)
        # Typed + registered tokens (audit 2026-08 H5): the refresh token
        # is a real refresh token (family-tracked, rotatable, revocable,
        # rejected as a bearer credential) and the access token carries a
        # session jti so logout-all kills the switched session too.
        issued = await issue_session(
            user,
            auth_mode=getattr(actor, "auth_mode", None) or "password",
            client_label="tenant-switch",
            extra_claims={
                "tenant_id": str(tenant.id),
                "original_tenant_id": str(actor.tenant_id),
                "original_user_id": str(user.id),
                "switched": True,
                "scoped_tenant_id": str(tenant.id),
            },
        )

        await log_audit_action(
            tenant_id=tenant.id,
            user_id=actor.user_id,
            action="tenant.switch_into",
            resource_type="tenant",
            resource_id=tenant.id,
        )
        return SwitchTenantResponse(
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            expires_in=issued.access_expires_in,
            scoped_tenant_id=tenant.id,
            original_tenant_id=actor.tenant_id,
            tenant=TenantResponse.model_validate(tenant),
        )

    async def switch_back(self, actor: Any) -> SwitchTenantResponse:
        """Restore the original SYSTEM_ADMIN session after a switch.

        §13 (plan 16 H7): demo-stamped principals are refused here too —
        symmetric with ``switch_into_tenant`` (they can never enter a
        switch, so this only fires on hand-minted claims; it keeps the
        surface closed either way).
        """
        _refuse_demo_principal(actor)
        if not getattr(actor, "switched", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current session is not a switched session.",
            )

        original_tenant_id = actor.original_tenant_id
        original_user_id = actor.original_user_id
        tenant = await self._get_tenant_or_404(original_tenant_id)

        user = await self._get_actor_user(actor)
        issued = await issue_session(
            user,
            auth_mode=getattr(actor, "auth_mode", None) or "password",
            client_label="tenant-switch-back",
        )

        await log_audit_action(
            tenant_id=original_tenant_id,
            user_id=original_user_id,
            action="tenant.switch_back",
            resource_type="tenant",
            resource_id=original_tenant_id,
        )
        return SwitchTenantResponse(
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            expires_in=issued.access_expires_in,
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
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserModel], int]:
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
        email: str | None,
        role: str,
        expires_days: int,
        actor_id: UUID | None = None,
    ) -> dict[str, Any]:
        if role == Role.SYSTEM_ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SYSTEM_ADMIN cannot be granted via invite.",
            )
        tenant = await self._get_tenant_or_404(tenant_id)
        expires_days = max(1, min(int(expires_days), 30))
        token, invite_jti_value = create_invite_token(
            tenant_id=str(tenant.id),
            email=email,
            role=role,
            expires_days=expires_days,
        )
        await token_store.register_invite(
            invite_jti_value, int(timedelta(days=expires_days).total_seconds())
        )
        # §17: invite minting is an admin action — audit it.
        await log_audit_action(
            tenant_id=tenant.id,
            user_id=actor_id,
            action="tenant.invite",
            resource_type="invite",
            resource_id=None,
            new_value={
                "role": role,
                "email": email,
                "expires_in_days": expires_days,
                "jti": invite_jti_value,
            },
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
        action: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        limit = max(1, min(limit, 250))
        offset = max(0, offset)
        query = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
        count_query = (
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
        )
        if action:
            query = query.where(AuditEvent.action == action)
            count_query = count_query.where(AuditEvent.action == action)
        if outcome:
            query = query.where(AuditEvent.outcome == outcome)
            count_query = count_query.where(AuditEvent.outcome == outcome)
        total = (await self.db.execute(count_query)).scalar() or 0
        query = (
            query.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
        )
        items = (await self.db.execute(query)).scalars().all()
        return list(items), int(total)

    async def list_audit_entries_all(
        self,
        *,
        tenant_id: UUID | None = None,
        action: str | None = None,
        outcome: str | None = None,
        user_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        """Cross-tenant audit viewer (identity-auth §17, plan 16 H2).

        SYSTEM_ADMIN-only at the endpoint layer. ``tenant_id=None`` returns
        every tenant's stream **plus** the system-level rows (NULL tenant —
        tenant deletes, auth denials for unknown principals).
        """
        limit = max(1, min(limit, 250))
        offset = max(0, offset)
        query = select(AuditEvent)
        count_query = select(func.count()).select_from(AuditEvent)
        if tenant_id is not None:
            query = query.where(AuditEvent.tenant_id == tenant_id)
            count_query = count_query.where(AuditEvent.tenant_id == tenant_id)
        if action:
            query = query.where(AuditEvent.action == action)
            count_query = count_query.where(AuditEvent.action == action)
        if outcome:
            query = query.where(AuditEvent.outcome == outcome)
            count_query = count_query.where(AuditEvent.outcome == outcome)
        if user_id is not None:
            query = query.where(AuditEvent.user_id == user_id)
            count_query = count_query.where(AuditEvent.user_id == user_id)
        total = (await self.db.execute(count_query)).scalar() or 0
        query = (
            query.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
        )
        items = (await self.db.execute(query)).scalars().all()
        return list(items), int(total)

"""Service-context actor resolution for integration-driven writes.

The clean service-layer functions that an integration will want to call
(``clinical_event_service.create_event``, the future
``examination_service.create_examination``, etc.) all take a
``current_user: TokenData`` parameter — both for RBAC (the ``check_*_access``
helpers compare against ``current_user.tenant_id`` / ``current_user.role``)
and for audit provenance (the ``AuditMixin.created_by`` /
``updated_by`` columns populate from ``current_user.user_id``).

There is no interactive user when a Celery worker runs
``sync_active_integrations``, when a webhook fires, or when a two-way API
proxy handler runs. Historically the only escape hatch was the bridge
provider bypassing the service layer entirely and writing ORM rows directly
— losing dedup, category resolution, doctor linking, audit provenance, and
tenant scoping.

This module provides a single helper, :func:`resolve_integration_actor`,
that derives a :class:`~app.schemas.user.TokenData` from a
:class:`~app.models.user_integration.UserIntegration`'s owning user. The
integration inherits its owner's identity for the duration of a sync, so
service-layer writes go through the same code path as interactive UI writes
(same RBAC, same audit, same tenant scoping). No service-account-style
"integration user" is created — the integration acts as its owner, with
whatever role the owner has.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)


async def resolve_integration_actor(
    db: AsyncSession, integration: UserIntegration
) -> TokenData:
    """Build a :class:`TokenData` representing the integration's owning user.

    The integration inherits the tenant_id and user_id of the
    :class:`UserIntegration` row (the user who connected it). The role is
    re-fetched from :class:`UserModel` on every call so role changes (e.g.
    an ADMIN demoted to USER after the integration was connected) take
    effect on the next sync without re-issuing tokens.

    The returned ``TokenData`` is a regular user-shaped token — service
    functions can ``check_patient_access(patient_id, current_user=actor, db)``
    against it exactly as they would against an interactive request's user,
    and ``AuditMixin`` columns populate with the owner's ``user_id`` so
    every integration-driven write is auditable to a real person.

    Raises:
        NotFoundError: if the owning user no longer exists (e.g. deleted
            after the integration was connected). The caller should surface
            this as an integration ERROR state — there's no valid identity
            to write under.
    """
    if integration.user_id is None:
        # Defensive — UserIntegration.user_id is non-nullable in the schema,
        # but a malformed row would otherwise produce a confusing query.
        raise NotFoundError(
            f"Integration {integration.id} has no owning user_id; cannot "
            "resolve a service-context actor."
        )

    user = await _fetch_owner(db, integration.user_id)
    role_value = user.role.value if user.role is not None else "USER"

    logger.debug(
        "Resolved integration actor: integration=%s owner=%s tenant=%s role=%s",
        integration.id, user.id, user.tenant_id, role_value,
    )

    return TokenData(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=role_value,
        sub=user.email,
        is_service_account=False,
    )


async def _fetch_owner(db: AsyncSession, user_id: UUID) -> UserModel:
    """Look up the integration's owning user. Raises NotFoundError if gone.

    Soft-deleted users (``is_deleted=True`` via SoftDeleteMixin, if applicable)
    are still returned — access checks downstream will refuse if the user's
    role no longer permits the write. The NotFoundError is reserved for the
    "row is gone entirely" case (hard delete / FK cascade).
    """
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user: Optional[UserModel] = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(
            f"Integration owner user {user_id} no longer exists — the "
            "integration should be marked ERROR; service-layer writes "
            "cannot proceed without a valid owning identity."
        )
    return user

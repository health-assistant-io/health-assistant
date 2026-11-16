"""Service-context actor resolution for integration-driven writes.

The clean service-layer functions an integration calls
(``clinical_event_service.create_event``, ``examination_service``, …) take a
``current_user: TokenData`` for RBAC and audit provenance. There is no
interactive user in a Celery sync, a webhook or a two-way API proxy handler.

Historically this module derived a ``TokenData`` from the integration's owning
user, so the integration exercised the owner's **whole role** (owner
impersonation). ADR 0054 D8 replaces that: every integration resolves a scoped
:class:`~app.models.integration_principal.IntegrationPrincipal` — a service
identity with its own permission subset (bounded by the extension manifest's
declared capabilities), patient scope and lifecycle. The owner stays the
accountable human (``user_id`` on writes and audit) but is no longer the
authorization identity.

The resolver lives in
:mod:`app.services.integration.integration_principals`; this module keeps the
single seam the engine imports, so every sync leg and provider path goes
through the same principal rules.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_integration import UserIntegration
from app.schemas.user import TokenData
from app.services.integration import integration_principals


async def resolve_integration_actor(db: AsyncSession, integration: UserIntegration) -> TokenData:
    """Resolve the integration's scoped principal (never its owner's role).

    The principal is provisioned on first use from the extension's declared
    capabilities, bounded by the integration's patient and the owner's live
    permissions. Each call re-intersects the stored scopes with the owner's
    live role and the manifest declaration, so a demotion, a manifest change
    or a revoked principal takes effect on the next sync. The actor carries
    the principal id and the owner id (audit attribution).

    Raises:
        AuthorizationError: no live principal, an empty scope intersection or
            a cross-tenant owner/integration. The caller surfaces it as an
            integration ERROR — there is no impersonation fallback.
        NotFoundError: the owning user or the integration row is gone.
    """
    return await integration_principals.resolve_actor(db, integration)


__all__ = ["resolve_integration_actor"]

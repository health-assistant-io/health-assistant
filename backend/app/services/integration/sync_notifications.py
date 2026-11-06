# ruff: noqa: E501 -- long immutable strings; reflow when touched
"""Integration sync notification emitters (baseline + provider-authored).

Moved verbatim out of integration_sync_service (plan 40 W2d): the sync
orchestrator keeps the pipeline; the notification surface lives beside the
other integration services."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.converters import utcnow as _now
from app.core.database import AsyncSessionLocal
from app.services.integration.sync_support import SyncResult, _declared

logger = logging.getLogger(__name__)


async def _notify_sync_outcome(integration: Any, result: SyncResult) -> None:
    """Surface sync results to the integration owner (best-effort).

    * New data arrived  → INTEGRATION_EVENT (info).
    * Auth/data failure → SYNC_FAILURE (warning) to owner + tenant admins.

    Rate-limit and skip outcomes are silent (transient / no action needed).
    """
    try:
        from sqlalchemy import select

        from app.models.enums import (
            NotificationCategory,
            NotificationSeverity,
            NotificationSource,
            NotificationType,
            RecipientKind,
            Role,
        )
        from app.models.user_model import UserModel
        from app.services.notifications.notification_service import emit

        tenant_id = integration.tenant_id
        targets: list[dict] = [{"kind": RecipientKind.USER.value, "id": str(integration.user_id)}]

        is_failure = result.status == "failed" and result.error_type in ("auth", "data")
        total_new = result.fhir_persisted + result.telemetry_persisted
        if not is_failure and total_new == 0:
            return  # nothing arrived and nothing failed — stay silent

        if is_failure:
            severity = NotificationSeverity.WARNING
            ntype = NotificationType.SYNC_FAILURE
            title = f"{integration.provider} sync failed"
            body = result.error or "The integration sync failed and may need re-authorization."
            async with AsyncSessionLocal() as session:
                admin_ids = [
                    row[0]
                    for row in (
                        await session.execute(
                            select(UserModel.id).where(
                                UserModel.tenant_id == tenant_id,
                                UserModel.role.in_(
                                    [
                                        Role.ADMIN.value,
                                        Role.MANAGER.value,
                                        Role.SYSTEM_ADMIN.value,
                                    ]
                                ),
                            )
                        )
                    ).all()
                ]
            for uid in admin_ids:
                targets.append({"kind": RecipientKind.USER.value, "id": str(uid)})
        else:
            severity = NotificationSeverity.INFO
            ntype = NotificationType.INTEGRATION_EVENT
            title = f"{integration.provider} synced {total_new} new record{'s' if total_new != 1 else ''}"
            body = f"{integration.instance_name}: {total_new} measurement(s) imported."

        await emit(
            source=NotificationSource.INTEGRATION,
            type=ntype,
            category=NotificationCategory.INTEGRATION,
            severity=severity,
            title=title,
            body=body,
            tenant_id=tenant_id,
            targets=targets,
            payload={
                "integration_id": str(integration.id),
                "provider": integration.provider,
            },
            source_ref={
                "integration_id": str(integration.id),
                "provider": integration.provider,
                "status": result.status,
            },
        )
    except Exception:
        logger.exception("Integration sync notification failed")


def _write_failed_log(
    db: AsyncSession,
    integration: Any,
    started: datetime,
    error: str,
    error_code: str | None = None,
    error_class: str | None = None,
) -> None:
    """Queue a ``failed`` IntegrationSyncLog row (caller commits).

    ``error_code`` is the charter code (``net-denied``, ``auth``, …) carried
    beside the capped detail as ``[code] detail``; ``error_class`` is the
    typed column routing reads (no message parsing).
    """
    from app.models.user_integration import IntegrationSyncLog

    db.add(
        IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status="failed",
            records_synced=0,
            started_at=started,
            completed_at=_now(),
            error_class=error_class or error_code,
            error_message=f"[{error_code}] {error}" if error_code else error,
        )
    )


async def _filter_specs_by_owner_type_prefs(integration: Any, specs: list[Any]) -> list[Any]:
    """Drop specs whose declared ``type_id`` the integration owner has muted.

    Per-integration-instance preferences live at
    ``user.settings["notifications.integration.{integration_id}.{type_id}"]
    = False``. The key is scoped to the integration **instance** (not the
    provider domain) so users can mute one Fitbit without muting another.
    Specs without a ``type_id`` always pass through. Loads the owner's
    settings once (single query); returns the input list unchanged on any
    error so a settings-lookup failure can never suppress notifications.
    """
    if not specs:
        return specs
    # Fast path: nothing is tagged.
    if not any(getattr(s, "type_id", None) for s in specs):
        return specs

    try:
        from app.models.user_model import UserModel

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(UserModel.settings).where(UserModel.id == integration.user_id)
                )
            ).scalar_one_or_none()
        user_settings = dict(row or {})
    except Exception:
        logger.exception(
            "Failed to load user settings for type pref filter; passing all specs through (user=%s)",
            integration.user_id,
        )
        return specs

    integration_id = integration.id
    out: list[Any] = []
    for spec in specs:
        tid = getattr(spec, "type_id", None)
        if not tid:
            out.append(spec)
            continue
        key = f"notifications.integration.{integration_id}.{tid}"
        if user_settings.get(key, True) is False:
            logger.debug(
                "Filtering integration notification (user=%s integration=%s type_id=%s — muted)",
                integration.user_id,
                integration_id,
                tid,
            )
            continue
        out.append(spec)
    return out


async def _emit_hitl_proposal_notification(integration: Any, spec: Any) -> None:
    """Fire the HITL notification for one newly-inserted proposal.

    Mirrors the chat-side ``_notify_hitl_proposal`` shape but keyed on
    ``NotificationSource.INTEGRATION`` so the frontend can render
    integration-sourced proposals separately if it wants. The action
    button links to the integration instance's proposals view
    (``/integrations/{provider}/{integration_id}/proposals/{proposal_id}``);
    the route doesn't exist in the SPA yet (frontend G.5 deferred per
    parent plan §G mitigation), so the link is forward-compatible — when
    G.5 lands it'll Just Work.

    Best-effort: failures are logged + swallowed so a buggy notification
    path can't break the sync.
    """
    try:
        from app.models.enums import (
            NotificationCategory,
            NotificationSeverity,
            NotificationSource,
            NotificationType,
            RecipientKind,
        )
        from app.services.notifications.notification_service import emit

        proposal_type = getattr(spec, "proposal_type", "proposal")
        title = getattr(spec, "title", "Integration proposal needs your review")
        await emit(
            source=NotificationSource.INTEGRATION,
            type=NotificationType.HITL_TASK,
            category=NotificationCategory.HITL,
            severity=NotificationSeverity.WARNING,
            title=title,
            body=(
                f"{integration.provider or 'Integration'} proposed a "
                f"{proposal_type.replace('_', ' ')} for your review."
            ),
            patient_id=getattr(spec, "patient_id", None),
            tenant_id=integration.tenant_id,
            targets=[
                {
                    "kind": RecipientKind.USER.value,
                    "id": str(integration.user_id),
                }
            ],
            payload={
                "proposal_type": proposal_type,
                "integration_id": str(integration.id),
                "domain": integration.provider,
                "proposed_payload": getattr(spec, "proposed_payload", {}),
            },
            source_ref={
                "integration_id": str(integration.id),
                "proposal_type": proposal_type,
                "source": "integration_hitl",
            },
        )
    except Exception:
        logger.exception(
            "HITL-proposal notification emit failed for integration=%s",
            getattr(integration, "id", None),
        )


async def _emit_provider_notifications(
    provider: Any,
    integration: Any,
    result: SyncResult,
    observations: list[Any],
) -> None:
    """Ask the provider for rich, event-driven notifications and emit them.

    Called from ``run_sync``'s ``finally`` block after a successful/partial
    sync, AND from the webhook handler (see :func:`post_sync_notifications`).
    The provider's :meth:`get_notifications` returns a list of
    :class:`~app.extensions.sdk.notifications.NotificationSpec`. The platform
    emits each with:

    * ``source=NotificationSource.INTEGRATION``
    * ``targets`` default = integration owner (USER); providers can override
      via ``spec.targets_override``
    * ``patient_id`` from the spec (defaults to ``integration.patient_id``)
    * ``tenant_id`` from the integration
    * ``source_ref.integration_id`` automatically added (admin filter/group)

    Failures are logged and swallowed — never propagate to the sync result.
    """
    if provider is None or not _declared(provider, "notify"):
        return

    context = {
        "sync_result": result,
        "patient_id": str(integration.patient_id) if integration.patient_id else None,
        "integration_id": str(integration.id),
        "domain": integration.provider,
    }
    try:
        specs = await provider.get_notifications(
            integration, observations=observations, context=context
        )
    except Exception:
        logger.exception(
            "Provider %s get_notifications raised; skipping",
            integration.provider,
        )
        return

    if not specs:
        return

    # Filter specs whose declared type_id the integration owner has opted
    # out of. Prefs live at user.settings["notifications.integration.
    # {domain}.{type_id}"] = False. Specs without a type_id always pass
    # through (backwards-compatible). When a spec uses targets_override
    # (broadcast beyond the owner), we still apply the owner's pref — the
    # owner is the one who configured the integration and is the canonical
    # audience; per-recipient type prefs would require pushing this filter
    # down into emit() and coupling it to integration concepts, which we
    # explicitly avoid.
    filtered_specs = await _filter_specs_by_owner_type_prefs(integration, specs)
    if not filtered_specs:
        return

    from app.models.enums import (
        NotificationCategory,
        NotificationSeverity,
        NotificationSource,
        NotificationType,
        RecipientKind,
    )
    from app.services.notifications.notification_service import emit

    for spec in filtered_specs:
        try:
            # Category / severity / type — accept both enum and string value.
            category = _coerce_enum(
                spec.category, NotificationCategory, NotificationCategory.INTEGRATION
            )
            severity = _coerce_enum(spec.severity, NotificationSeverity, NotificationSeverity.INFO)
            ntype = _coerce_enum(spec.type, NotificationType, NotificationType.INTEGRATION_EVENT)

            patient_id = spec.patient_id or integration.patient_id
            targets = spec.targets_override or [
                {"kind": RecipientKind.USER.value, "id": str(integration.user_id)}
            ]

            source_ref = dict(spec.source_ref)
            source_ref.setdefault("integration_id", str(integration.id))
            source_ref.setdefault("provider", integration.provider)
            # Propagate the spec's declared type_id so the kind registry can
            # stamp ``integration:{iid}:{type_id}`` into payload.preferences
            # at emit time (drives the per-instance "Turn off this kind"
            # button). Only set when present; un-typed specs fall through to
            # the source-level INTEGRATION kind.
            if getattr(spec, "type_id", None):
                source_ref.setdefault("type_id", spec.type_id)

            await emit(
                source=NotificationSource.INTEGRATION,
                type=ntype,
                category=category,
                severity=severity,
                title=spec.title,
                body=spec.body,
                patient_id=patient_id,
                tenant_id=integration.tenant_id,
                targets=targets,
                payload=spec.to_payload(),
                source_ref=source_ref,
                sender_user_id=None,
                # Item 4 of integrations-sdk-improvements: forward the
                # provider's digest_key so consecutive syncs collapse
                # into one inbox entry inside the TTL window.
                dedup_key=getattr(spec, "digest_key", None),
            )
        except Exception:
            logger.exception(
                "Failed to emit provider notification %r for %s",
                getattr(spec, "title", "?"),
                integration.id,
            )
            continue


async def post_sync_notifications(
    provider: Any,
    integration: Any,
    *,
    pulled: int,
    fhir_persisted: int,
    telemetry_persisted: int,
    status: str,
    started_at: datetime,
    completed_at: datetime,
    error: str | None = None,
    error_type: str | None = None,
    observations: list[Any] | None = None,
) -> None:
    """Best-effort baseline + provider-authored notification dispatch.

    Single entry point called from BOTH ``run_sync``'s finally block AND
    the webhook endpoint so webhook-driven integrations get the same
    notification coverage as pull-driven ones. Constructs a throwaway
    :class:`SyncResult` and invokes both emitters. Never raises — wraps
    every step in try/except so a notification failure can never break
    the parent sync / webhook response.
    """
    result = SyncResult(
        pulled=pulled,
        fhir_persisted=fhir_persisted,
        telemetry_persisted=telemetry_persisted,
        status=status,
        error=error,
        error_type=error_type,
        started_at=started_at,
        completed_at=completed_at,
    )
    try:
        await _notify_sync_outcome(integration, result)
    except Exception:
        logger.exception("Sync-outcome notification failed for %s", integration.id)
    if status in ("success", "partial") and (fhir_persisted + telemetry_persisted) > 0:
        try:
            await _emit_provider_notifications(provider, integration, result, observations or [])
        except Exception:
            logger.exception("Provider-authored notifications failed for %s", integration.id)


def _coerce_enum(value: Any, enum_cls: Any, default: Any) -> Any:
    """Best-effort coerce a string or enum member to the enum class."""
    if value is None:
        return default
    try:
        return enum_cls(value)
    except Exception:
        try:
            return enum_cls[value]
        except Exception:
            return default

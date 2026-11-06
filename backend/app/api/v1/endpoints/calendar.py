
"""Calendar connector API (ADR-0052 / plan 52 P2).

The settings surface for health's calendar bindings: the provider
**catalog** (data from the vendored family catalog — never app copy),
binding CRUD, secret set, connect test, sync-now, the class-S
server-side OAuth round-trip (state + PKCE, host-owned callback route),
and the inbound confirmation surface for pulled events.

Ownership: a binding is tenant-, user- and patient-scoped; every query
filters ``tenant_id`` + ``user_id`` (+ ``patient_id`` when given), so a
user may only touch their own bindings — the de-facto ownership check the
integrations endpoints use (``(id, user_id, tenant_id)`` → 404 otherwise).

Validation: provider/service/auth kind are validated against the catalog
(unknown provider ⇒ 422); ``direction``/``redaction`` use closed
vocabularies; ``redaction='full'`` is **consent-gated** — the request must
carry ``consent_full_details=true``; consent notices are keyed to the
**chosen** auth kind (Rev 3 E12 — a token ICS bind must never read "needs
no credential"); one writer per remote calendar (409 on a second writable
binding over the same calendar); an address whose query string is a
secret keeps only scheme+host+path in the row (C4).

Custody: responses carry the ``secret_ref`` pointer and slot metadata
(kid/expiry) but never credential material — every error detail is
scrubbed (URL query strings stripped) before it reaches the wire.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import oauth_clients
from app.core.database import get_db
from app.core.security import get_current_user
from app.extensions.cloud.cloud_providers import (
    ACCOUNT_STATES,
    CatalogError,
    default_catalog,
    provider_rows,
    resolve_provider,
)
from app.extensions.errors import ExtensionError
from app.extensions.sdk.auth import OAuthStateStore, exchange_code, generate_pkce, generate_state
from app.extensions.sdk.secrets import SecretCipher
from app.models.calendar_binding import (
    DIRECTIONS,
    REDACTION_PROFILES,
    CalendarBinding,
)
from app.models.clinical_event import ClinicalEvent
from app.models.fhir.patient import Patient
from app.schemas.user import TokenData
from app.services.calendar import credentials as binding_credentials
from app.services.calendar import sync as calendar_sync
from app.services.calendar.sanitize import sanitize_error_detail, sanitize_url
from app.services.calendar.url_custody import (
    URL_KEYS,
    custody_check,
    derive_remote_calendar_id,
    normalize_webcal,
    split_url_custody,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: A binding whose last success is older than this renders ``stale`` (the
#: family surfaces vocabulary; the column itself keeps the engine state).
STALE_AFTER_HOURS = 48

_REDACTION_COPY = {
    "none": "Publishes a bare time block only — the calendar shows you are busy and nothing else.",
    "neutral": (
        "Publishes \u201cMedical appointment\u201d — no title, notes, location or"
        " category detail. Default."
    ),
    "full": (
        "Publishes the real title, notes and location. Opt-in: this sends"
        " health detail to the provider."
    ),
}


def _catalog():
    return default_catalog()


def _frontend_origin() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")


def _oauth_client_ids() -> dict[str, str]:
    return oauth_clients.client_ids()


def _validate_provider(provider_id: str, auth_kind: str | None) -> tuple[Any, str]:
    """Resolve + validate a provider row and its auth kind (422 on drift)."""
    catalog = _catalog()
    try:
        provider = resolve_provider(catalog, provider_id, "calendar")
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    kind = auth_kind or provider.auth_kinds[0]
    if kind not in provider.auth_kinds:
        raise HTTPException(
            status_code=422,
            detail=f"Provider {provider_id} does not accept auth kind {kind!r} "
            f"(accepts {list(provider.auth_kinds)})",
        )
    if kind not in ("oauth2", "app-password", "token", "none"):
        raise HTTPException(status_code=422, detail=f"Unknown auth kind {kind!r}")
    return provider, kind


def _validate_redaction(redaction: str, consent_full_details: bool) -> str:
    if redaction not in REDACTION_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=f"redaction must be one of {list(REDACTION_PROFILES)}",
        )
    if redaction == "full" and not consent_full_details:
        raise HTTPException(
            status_code=422,
            detail=(
                "redaction='full' publishes real titles, notes and locations to "
                "the provider; set consent_full_details=true to opt in."
            ),
        )
    return redaction


async def _load_owned_binding(
    binding_id: str, *, current_user: TokenData, db: AsyncSession
) -> CalendarBinding:
    try:
        binding_uuid = UUID(binding_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid binding ID format") from exc
    stmt = select(CalendarBinding).where(
        CalendarBinding.id == binding_uuid,
        CalendarBinding.user_id == current_user.user_id,
        CalendarBinding.tenant_id == current_user.tenant_id,
    )
    binding = (await db.execute(stmt)).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="Calendar binding not found")
    return binding


def _effective_status(binding: CalendarBinding, now: Any) -> str:
    """The surface status: engine state, or ``stale`` when too quiet.

    A binding that has never synced is not stale — it has no history to
    go quiet against; ``stale`` renders once a previously-successful
    binding's last success is older than the freshness horizon.
    """
    if (
        binding.enabled
        and binding.status == "ok"
        and binding.last_synced_at is not None
        and (now - binding.last_synced_at).total_seconds() > STALE_AFTER_HOURS * 3600
    ):
        return "stale"
    return binding.status


async def _binding_surface(db: AsyncSession, binding: CalendarBinding) -> dict[str, Any]:
    from app.core.converters import utcnow

    # Defense in depth: a stored ``last_error`` is already scrubbed at the
    # sync boundary — the surface scrubs again so no URL query string can
    # ever reach the wire, whatever wrote the column.
    options = dict(binding.options or {})
    for key in URL_KEYS:
        value = options.get(key)
        if isinstance(value, str) and value:
            options[key] = sanitize_url(value)
    return {
        "id": str(binding.id),
        "tenant_id": str(binding.tenant_id),
        "user_id": str(binding.user_id),
        "patient_id": str(binding.patient_id),
        "provider": binding.provider,
        "service": binding.service,
        "auth_kind": binding.auth_kind,
        "label": binding.label,
        "account_label": binding.account_label,
        "scopes": list(binding.scopes or []),
        "secret_ref": binding.secret_ref,
        "direction": binding.direction,
        "redaction": binding.redaction,
        "enabled": binding.enabled,
        "status": _effective_status(binding, utcnow()),
        "last_error": sanitize_error_detail(binding.last_error),
        "last_synced_at": binding.last_synced_at,
        "consecutive_failures": binding.consecutive_failures,
        "remote_calendar_id": binding.remote_calendar_id,
        "options": options,
        "credentials": await binding_credentials.slots_summary(db, binding),
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/providers")
async def list_calendar_providers(
    current_user: TokenData = Depends(get_current_user),
) -> dict[str, Any]:
    """The calendar provider catalog — data, no secrets, no client ids.

    Consent notices are keyed by the **chosen** auth kind (Rev 3 E12): a
    ``token``-authenticated private ICS bind must never see "needs no
    credential" — the notice the catalog renders for ``auth_kinds[0]``
    would say exactly that. The surface therefore carries a verbatim
    ``consentNotices`` map (one entry per accepted kind) and the bind form
    selects the entry for the kind being bound.
    """
    catalog = _catalog()
    rows = provider_rows(catalog, "calendar")
    notices_by_provider = {
        provider.id: {kind: provider.consent_notice(kind) for kind in provider.auth_kinds}
        for provider in catalog.for_service("calendar")
    }
    for row in rows:
        row["consentNotices"] = notices_by_provider.get(row["id"], {})
    return {
        "providers": rows,
        "redactionProfiles": [
            {"id": profile, "description": _REDACTION_COPY[profile]}
            for profile in REDACTION_PROFILES
        ],
        "redactionDefault": "neutral",
        "directions": list(DIRECTIONS),
        "accountStates": list(ACCOUNT_STATES),
    }


# ---------------------------------------------------------------------------
# Binding CRUD
# ---------------------------------------------------------------------------


class BindingCreate(BaseModel):
    provider: str
    patient_id: str
    auth_kind: str | None = None
    label: str | None = Field(default=None, max_length=255)
    account_label: str | None = Field(default=None, max_length=255)
    direction: str = "push"
    redaction: str = "neutral"
    consent_full_details: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class BindingUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    account_label: str | None = Field(default=None, max_length=255)
    direction: str | None = None
    redaction: str | None = None
    consent_full_details: bool = False
    enabled: bool | None = None
    options: dict[str, Any] | None = None


class SecretSet(BaseModel):
    value: dict[str, Any]


@router.get("/bindings")
async def list_bindings(
    patient_id: str | None = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """The caller's own bindings (tenant + user scoped, optional patient)."""
    stmt = select(CalendarBinding).where(
        CalendarBinding.tenant_id == current_user.tenant_id,
        CalendarBinding.user_id == current_user.user_id,
    )
    if patient_id:
        stmt = stmt.where(CalendarBinding.patient_id == patient_id)
    bindings = (await db.execute(stmt)).scalars().all()
    return [await _binding_surface(db, binding) for binding in bindings]


@router.post("/bindings")
async def create_binding(
    payload: BindingCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Bind a provider calendar for one patient record."""
    provider, auth_kind = _validate_provider(payload.provider, payload.auth_kind)
    if payload.direction not in DIRECTIONS:
        raise HTTPException(
            status_code=422, detail=f"direction must be one of {list(DIRECTIONS)}"
        )
    redaction = _validate_redaction(payload.redaction, payload.consent_full_details)

    try:
        patient_uuid = UUID(payload.patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid patient ID format") from exc
    patient = (
        await db.execute(
            select(Patient).where(
                Patient.id == patient_uuid, Patient.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=400, detail="Invalid Patient record.")

    custody_required: list[str] = []
    if payload.options:
        options, custody_required = _validate_options(provider, auth_kind, payload.options)
    else:
        options = {}

    remote_calendar_id = _remote_calendar_id_for(provider, options)
    await _refuse_second_writer(
        db,
        current_user,
        patient_uuid,
        payload.provider,
        remote_calendar_id,
        new_direction=payload.direction,
    )

    binding_id = uuid4()
    slot = binding_credentials.slot_for(auth_kind)
    binding = CalendarBinding(
        id=binding_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        patient_id=patient_uuid,
        provider=payload.provider,
        service="calendar",
        auth_kind=auth_kind,
        label=payload.label or provider.label,
        account_label=payload.account_label,
        scopes=list(provider.service("calendar").effective_scopes("write"))
        if auth_kind == "oauth2"
        else None,
        secret_ref=binding_credentials.secret_ref_for(binding_id, auth_kind),
        direction=payload.direction,
        redaction=redaction,
        status="unbound" if slot else "ok",
        remote_calendar_id=remote_calendar_id,
        options=options,
    )
    db.add(binding)
    try:
        await db.commit()
    except IntegrityError as exc:
        # The partial unique index is the race backstop for the pre-check.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This remote calendar already has a writer binding.",
        ) from exc
    await db.refresh(binding)
    surface = await _binding_surface(db, binding)
    if custody_required:
        # The row keeps scheme+host+path only — seal the full URLs now.
        surface["url_custody_required"] = custody_required
    return surface


def _validate_options(
    provider: Any, auth_kind: str, options: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Validate + normalize options; return (sanitized, custody_required).

    Three laws enforced here:

    * options are non-secret transport configuration only — a credential
      key is a 422;
    * an address whose query string makes it a **secret** (C4) is reduced
      to scheme+host+path in the row; the caller must seal the full URL
      into the slot (the returned list names those keys);
    * inbound acceptance options are typed — ``accept_all`` boolean,
      ``accept_categories`` a list of strings (never "the whole calendar"
      by accident).
    """
    forbidden = {"password", "token", "access_token", "refresh_token", "client_secret"}
    leaked = sorted(forbidden & set(options))
    if leaked:
        raise HTTPException(
            status_code=422,
            detail=f"options must not carry credential material ({', '.join(leaked)}); "
            "use POST /calendar/bindings/{id}/secret",
        )

    accept_all = options.get("accept_all", options.get("acceptAll"))
    if accept_all is not None and not isinstance(accept_all, bool):
        raise HTTPException(
            status_code=422, detail="options.accept_all must be a boolean"
        )
    accept_categories = options.get(
        "accept_categories", options.get("acceptCategories")
    )
    if accept_categories is not None and (
        not isinstance(accept_categories, list)
        or any(not isinstance(item, str) for item in accept_categories)
    ):
        raise HTTPException(
            status_code=422,
            detail="options.accept_categories must be a list of strings",
        )

    sanitized = dict(options)
    for key in URL_KEYS:
        value = sanitized.get(key)
        if isinstance(value, str) and value.strip():
            sanitized[key] = normalize_webcal(value)

    custody = custody_check(sanitized)
    if custody and binding_credentials.slot_for(auth_kind) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "A URL with a query string is a credential (ADR-0052 §9): bind "
                "this source with the 'token' auth kind so the full feed URL "
                "can be sealed — the row keeps only scheme+host+path."
            ),
        )
    for key, full_url in custody.items():
        sanitized[key] = split_url_custody(full_url)[0]
    return sanitized, sorted(custody)


def _remote_calendar_id_for(provider: Any, options: dict[str, Any]) -> str | None:
    spec = provider.service("calendar")
    return derive_remote_calendar_id(spec.protocol, options)


async def _refuse_second_writer(
    db: AsyncSession,
    current_user: TokenData,
    patient_id: UUID,
    provider_id: str,
    remote_calendar_id: str | None,
    *,
    new_direction: str = "push",
    exclude_binding_id: UUID | None = None,
) -> None:
    """One writer per remote calendar (ADR-0052 §9, Rev 3 H4).

    Only a **writable** newcomer (``push``/``two-way``) over an
    already-written calendar is a 409; read-only subscriptions may share
    freely — with a writer or with each other.
    """
    if not remote_calendar_id or new_direction not in ("push", "two-way"):
        return
    stmt = select(CalendarBinding).where(
        CalendarBinding.tenant_id == current_user.tenant_id,
        CalendarBinding.patient_id == patient_id,
        CalendarBinding.provider == provider_id,
        CalendarBinding.remote_calendar_id == remote_calendar_id,
        CalendarBinding.direction.in_(("push", "two-way")),
    )
    if exclude_binding_id is not None:
        stmt = stmt.where(CalendarBinding.id != exclude_binding_id)
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This remote calendar already has a writer binding "
                f"({existing.label}). One writer per calendar — bind it "
                "read-only (direction 'pull') or disconnect the other binding."
            ),
        )


@router.patch("/bindings/{binding_id}")
async def update_binding(
    binding_id: str,
    payload: BindingUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    custody_required: list[str] = []
    if payload.direction is not None:
        if payload.direction not in DIRECTIONS:
            raise HTTPException(
                status_code=422, detail=f"direction must be one of {list(DIRECTIONS)}"
            )
        # A direction flip to writable needs the one-writer check BEFORE the
        # mutation — the check's SELECT would autoflush the new direction
        # and trip the unique index instead of returning a typed 409.
        if payload.direction in ("push", "two-way") and binding.remote_calendar_id:
            await _refuse_second_writer(
                db,
                current_user,
                binding.patient_id,
                binding.provider,
                binding.remote_calendar_id,
                exclude_binding_id=binding.id,
            )
        binding.direction = payload.direction
    if payload.redaction is not None:
        binding.redaction = _validate_redaction(payload.redaction, payload.consent_full_details)
    if payload.label is not None:
        binding.label = payload.label
    if payload.account_label is not None:
        binding.account_label = payload.account_label
    if payload.enabled is not None:
        binding.enabled = payload.enabled
    if payload.options is not None:
        provider, _ = _validate_provider(binding.provider, binding.auth_kind)
        options, custody_required = _validate_options(provider, binding.auth_kind, payload.options)
        remote_calendar_id = _remote_calendar_id_for(provider, options)
        if binding.direction in ("push", "two-way"):
            await _refuse_second_writer(
                db,
                current_user,
                binding.patient_id,
                binding.provider,
                remote_calendar_id,
                exclude_binding_id=binding.id,
            )
        binding.options = options
        binding.remote_calendar_id = remote_calendar_id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This remote calendar already has a writer binding.",
        ) from exc
    await db.refresh(binding)
    surface = await _binding_surface(db, binding)
    if payload.options is not None and custody_required:
        surface["url_custody_required"] = custody_required
    return surface


@router.delete("/bindings/{binding_id}")
async def delete_binding(
    binding_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Unbind: revoke best-effort, delete every secret slot and the row.

    Product records (examinations, clinical events) are untouched — the
    links table cascades, the clinical data survives (ADR-0052 §9).
    """
    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)

    revoked_at_provider = False
    if binding.auth_kind == "oauth2":
        revoked_at_provider = await _revoke_remote(binding)

    slots_deleted = await binding_credentials.delete_secrets(db, binding)
    await db.delete(binding)
    await db.commit()
    return {
        "message": "Calendar binding removed.",
        "secrets_deleted": slots_deleted,
        "revoked_at_provider": revoked_at_provider,
    }


async def _revoke_remote(binding: CalendarBinding) -> bool:
    """Best-effort RFC 7009 revoke using the catalog's revoke URL."""
    import httpx

    from app.extensions.runtime.http import guarded_request

    try:
        provider = resolve_provider(_catalog(), binding.provider, binding.service)
        revoke_url = provider.oauth.revoke_url if provider.oauth else None
        if not revoke_url:
            return False
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
            response = await guarded_request(http, "POST", revoke_url)
            return response.status_code < 400
    except Exception as exc:
        logger.warning(
            "Calendar token revocation for binding %s failed (best-effort): %s",
            binding.id,
            # Scrubbed: exception text can carry a full URL (C4/E12).
            sanitize_error_detail(str(exc)),
        )
        return False


# ---------------------------------------------------------------------------
# Secret custody
# ---------------------------------------------------------------------------


@router.post("/bindings/{binding_id}/secret")
async def set_binding_secret(
    binding_id: str,
    payload: SecretSet,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Store the binding's credential in its sealed slot.

    ``oauth2`` bindings must use the OAuth flow (``POST …/oauth/start``);
    ``app-password`` takes ``{"password": …, "username": …}`` and ``token``
    takes ``{"token": …}``. The plaintext never touches an unsealed column
    and is never echoed back.
    """
    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    if binding.auth_kind == "oauth2":
        raise HTTPException(
            status_code=400,
            detail="This provider uses OAuth — start the flow at POST "
            f"/calendar/bindings/{binding_id}/oauth/start",
        )
    if binding.auth_kind == "none":
        raise HTTPException(status_code=400, detail="This binding needs no secret.")
    try:
        SecretCipher.from_settings()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await binding_credentials.set_secret(db, binding, payload.value)
    except ExtensionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    binding.status = "ok"
    binding.last_error = None
    binding.consecutive_failures = 0
    await db.commit()
    await db.refresh(binding)
    return await _binding_surface(db, binding)


# ---------------------------------------------------------------------------
# Test + sync
# ---------------------------------------------------------------------------


@router.post("/bindings/{binding_id}/test")
async def test_binding_route(
    binding_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Connectivity probe — a bounded list call, no writes."""
    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    result = await calendar_sync.test_binding(db, binding)
    return result


@router.post("/bindings/{binding_id}/sync")
async def sync_binding_route(
    binding_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run one bounded sync now. The response carries counts only — never
    event titles or record data."""
    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    outcome = await calendar_sync.run_sync(db, binding, source="manual")
    return outcome.to_dict()


@router.get("/bindings/{binding_id}/logs")
async def binding_sync_logs(
    binding_id: str,
    limit: int = 20,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Recent sync-run audit rows (counts + typed codes only)."""
    from app.models.calendar_binding import CalendarSyncLog

    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    rows = (
        await db.execute(
            select(CalendarSyncLog)
            .where(CalendarSyncLog.binding_id == binding.id)
            .order_by(CalendarSyncLog.started_at.desc())
            .limit(max(1, min(limit, 100)))
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "status": row.status,
            "counts": dict(row.counts or {}),
            "skipped": row.skipped,
            "error_code": row.error_code,
            "error_detail": sanitize_error_detail(row.error_detail),
            "source": row.source,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Inbound confirmation (C3 — pulled events are untrusted until confirmed)
# ---------------------------------------------------------------------------


async def _load_owned_inbound_event(
    binding_id: str,
    clinical_event_id: str,
    *,
    current_user: TokenData,
    db: AsyncSession,
) -> ClinicalEvent:
    """The calendar-origin clinical event behind one binding — owned only.

    Scoped on tenant + user + the binding's patient (the binding row is
    loaded owned first, so a stranger cannot confirm anyone's record even
    by guessing ids).
    """
    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    try:
        event_uuid = UUID(clinical_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid event ID format") from exc
    row = (
        await db.execute(
            select(ClinicalEvent).where(
                ClinicalEvent.id == event_uuid,
                ClinicalEvent.tenant_id == binding.tenant_id,
                ClinicalEvent.patient_id == binding.patient_id,
                ClinicalEvent.source_integration_id.is_(None),
                ClinicalEvent.event_metadata["origin"].astext == "calendar",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Inbound calendar event not found.")
    return row


@router.post("/bindings/{binding_id}/inbound/{clinical_event_id}/confirm")
async def confirm_inbound_event(
    binding_id: str,
    clinical_event_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Confirm a pulled event into the clinical record (ADR-0015 Tier 1).

    Clears the ``unverified`` flag the pull created the record with — the
    human confirmation that promotes untrusted calendar text to verified
    clinical data. No event content is returned (a confirmation response
    carries states, not clinical text).
    """
    row = await _load_owned_inbound_event(
        binding_id, clinical_event_id, current_user=current_user, db=db
    )
    metadata = dict(row.event_metadata or {})
    metadata["unverified"] = False
    metadata["confirmed_by"] = str(current_user.user_id)
    row.event_metadata = metadata
    await db.commit()
    return {
        "id": str(row.id),
        "unverified": False,
        "status": row.status.value if hasattr(row.status, "value") else row.status,
    }


@router.post("/bindings/{binding_id}/inbound/{clinical_event_id}/dismiss")
async def dismiss_inbound_event(
    binding_id: str,
    clinical_event_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Reject a pulled event: soft-delete the calendar-origin record.

    The link row stays until the next sync unlinks it (the remote event
    remains and may be re-proposed); the record itself is removed from the
    patient's view without destroying history (soft delete).
    """
    from app.core.converters import utcnow

    assert utcnow is not None
    row = await _load_owned_inbound_event(
        binding_id, clinical_event_id, current_user=current_user, db=db
    )
    row.deleted_at = row.deleted_at or utcnow()
    await db.commit()
    return {"id": str(row.id), "dismissed": True}


# ---------------------------------------------------------------------------
# OAuth round-trip (class S: the server owns the callback route)
# ---------------------------------------------------------------------------


@router.post("/bindings/{binding_id}/oauth/start")
async def oauth_start(
    binding_id: str,
    request: Any = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Begin the OAuth authorization-code + PKCE flow.

    Returns ``authorize_url`` + ``state``; the verifier lives only in the
    Redis state store (one in-flight flow per binding, ~10-minute TTL).
    Missing deployment configuration (no registered public client id) is a
    typed 400 ``needs-config`` refusal — never a crash.
    """
    from fastapi import Request

    binding = await _load_owned_binding(binding_id, current_user=current_user, db=db)
    provider, _ = _validate_provider(binding.provider, binding.auth_kind)
    if binding.auth_kind != "oauth2" or provider.oauth is None:
        raise HTTPException(status_code=400, detail="This provider does not use OAuth.")

    client_id = _oauth_client_ids().get(binding.provider)
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"needs-config: no public OAuth client id is configured for provider "
                f"{binding.provider!r} (CALENDAR_OAUTH_CLIENT_IDS)"
            ),
        )

    from app.core.config import get_settings

    app_url = (
        (get_settings().APP_URL or "")
        or (str(request.base_url).rstrip("/") if isinstance(request, Request) else "")
    ).rstrip("/")
    redirect_uri = f"{app_url}/api/v1/calendar/oauth/callback"

    scope_preset = "read" if binding.direction == "pull" else "write"
    scopes = provider.oauth_scopes(binding.service, scope_preset)
    verifier, challenge, _ = generate_pkce()
    state = generate_state()
    await OAuthStateStore().issue(
        state,
        {
            "binding_id": str(binding.id),
            "user_id": str(current_user.user_id),
            "tenant_id": str(current_user.tenant_id),
            "provider": binding.provider,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "token_url": provider.oauth.token_url,
            "scope": " ".join(scopes),
        },
    )
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(scopes),
    }
    authorize_url = provider.oauth.authorize_url + (
        "&" if "?" in provider.oauth.authorize_url else "?"
    ) + urlencode(params)
    return {"authorize_url": authorize_url, "state": state}


@router.get("/oauth/callback")
async def oauth_callback(
    state: str,
    code: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """OAuth callback (browser redirect) — secured by state **and session**.

    The bind-CSRF law (ADR-0052 §3, Rev 3 E12): ``state`` is bound to the
    authenticated principal that started the flow, single-use with a short
    expiry. The completing browser must therefore be the same signed-in
    user — a state issued to another principal is refused (403) and the
    state is burned (the one-shot consume happens first), so the code it
    carries can never complete against anyone's binding. Without the
    session-bound check an attacker could complete *their* authorization
    code against a *victim's* binding — pointing the victim's clinical
    calendar at the attacker's account (an exfiltration channel).
    """
    import httpx
    from fastapi import Response

    pending = await OAuthStateStore().consume(state)
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    binding_id = pending.get("binding_id")
    user_id = pending.get("user_id")
    tenant_id = pending.get("tenant_id")
    if not binding_id or not user_id or not tenant_id:
        raise HTTPException(status_code=400, detail="Malformed OAuth state payload.")
    try:
        binding_uuid = UUID(str(binding_id))
        user_uuid = UUID(str(user_id))
        tenant_uuid = UUID(str(tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed identifiers in state.") from exc

    # The completing session must be the principal that started the flow.
    if (
        current_user.user_id != user_uuid
        or current_user.tenant_id != tenant_uuid
    ):
        raise HTTPException(
            status_code=403,
            detail="This OAuth state belongs to a different session — flow refused.",
        )

    binding = (
        await db.execute(
            select(CalendarBinding).where(
                CalendarBinding.id == binding_uuid,
                CalendarBinding.user_id == user_uuid,
                CalendarBinding.tenant_id == tenant_uuid,
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="Calendar binding not found.")

    landing = f"{_frontend_origin()}/settings/integrations"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
            token = await exchange_code(
                pending["token_url"],
                code,
                pending["code_verifier"],
                pending["redirect_uri"],
                pending["client_id"],
                client_secret=oauth_clients.client_secret_for(pending["provider"]),
                http=http,
            )
    except Exception:
        return Response(
            status_code=302,
            headers={
                "Location": f"{landing}?calendar_binding={binding_id}&status=error"
            },
        )

    scopes = _scope_list(token.get("scope")) or _scope_list(pending.get("scope"))
    await binding_credentials.set_secret(
        db,
        binding,
        {
            "access_token": token.get("access_token"),
            "refresh_token": token.get("refresh_token"),
            "expires_at": token.get("expires_at"),
            "scope": " ".join(scopes),
            "token_endpoint": pending["token_url"],
            "client_id": pending["client_id"],
        },
        scopes=scopes,
        access_expires_at=calendar_sync.parse_expires(token.get("expires_at")),
    )
    binding.scopes = scopes
    binding.status = "ok"
    binding.last_error = None
    binding.consecutive_failures = 0
    await db.commit()
    return Response(
        status_code=302,
        headers={"Location": f"{landing}?calendar_binding={binding_id}&status=connected"},
    )


def _scope_list(scope: Any) -> list[str]:
    if isinstance(scope, str):
        return [item for item in scope.replace(",", " ").split() if item]
    if isinstance(scope, (list, tuple)):
        return [str(item) for item in scope]
    return []

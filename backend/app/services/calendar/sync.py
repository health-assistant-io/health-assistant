"""Calendar sync: pure reconcile + transactional apply (ADR-0052 §5, plan 52 P2).

Pipeline per run (one lock per binding):

1. **lock** — Redis ``SET NX EX`` keyed by binding id, degrade-open when
   Redis is down (the ``run_sync`` posture);
2. **credential gate** — :func:`app.services.calendar.credentials.require_secret`
   fails closed (typed ``needs-secret`` when no usable slot or no data key;
   the OAuth path refreshes on use and maps ``invalid_grant`` to
   ``needs-auth``, ``403 insufficient_scope`` to ``needs-secret`` — the
   ADR-0052 §3 failure posture, notify-once for typed stops);
3. **transport** — built from the vendored catalog
   (:func:`app.extensions.cloud.cloud_calendar.transport_for`) keyed by
   the provider's calendar protocol, over the **guarded** client seam
   (:class:`app.services.calendar.net.GuardedCalendarClient` — resolve-
   then-pin SSRF guard on every hop, size cap; a private feed's full URL is
   rehydrated from its sealed slot, the row keeps scheme+host+path, C4);
   auth is injected per call, so no token ever enters the transport's state;
4. **window** — bounded: 14 days back / 400 days forward (the family
   production defaults), page-capped, and the window is passed to
   reconcile (E5 — an out-of-window link is absent, not deleted);
5. **reconcile** — the vendored **pure** :func:`reconcile` produces the
   typed plan (no I/O, no mutation); conflicts default to **skip** (C2),
   deletions to keep-local; inbound acceptance is the host predicate —
   ``health:`` round-trip uids only, unless the link explicitly opts in
   with ``acceptCategories``/``acceptAll`` (a personal calendar is never
   pulled wholesale);
6. **apply** — the plan is applied transactionally and idempotently
   (separate ``local_hash``/``remote_hash`` pins per link row, E3);
   pulled events become ``origin='calendar'`` records **marked
   unverified** that enter the record only through the confirmation
   surface (C3); records the local user created are never clobbered by
   remote writes;
7. **bookkeeping** — status transitions per the failure posture
   (``ok`` / ``degraded`` / ``failing`` / ``needs-auth`` /
   ``needs-secret``; ``stale`` is rendered read-side), the opaque sync
   cursor round-trips, transient failure streaks count toward the cap and
   stop, terminal stops notify **once**, and one audit row
   (``calendar_sync_logs``) records counts + typed codes only — every
   detail is scrubbed (no URL query strings, no PHI).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import oauth_clients
from app.core.converters import utcnow as _now
from app.extensions.cloud.cloud_calendar import (
    ActionKind,
    CalendarError,
    CalendarEvent,
    CalendarPage,
    CalendarTransport,
    ConflictPolicy,
    DeletionPolicy,
    EventLink,
    LocalEvent,
    SyncDirection,
    SyncPlan,
    basic_auth,
    bearer_auth,
    event_hash,
    is_attributable,
    reconcile,
    transport_for,
)
from app.extensions.cloud.cloud_providers import default_catalog, resolve_provider
from app.extensions.errors import ExtensionError
from app.models.calendar_binding import CalendarEventLink, CalendarSyncLog
from app.models.clinical_event import ClinicalEvent, ClinicalEventOccurrence, ClinicalEventStatus
from app.models.examination_model import ExaminationModel
from app.services.calendar import credentials as binding_credentials
from app.services.calendar.credentials import CalendarCredentialError
from app.services.calendar.mapping import (
    NEUTRAL_SUMMARY,
    Projection,
    clinical_event_event,
    examination_event,
    exclusive_end,
    occurrence_event,
)
from app.services.calendar.net import wrap_guarded
from app.services.calendar.sanitize import sanitize_error_detail

logger = logging.getLogger(__name__)

#: The family production window (ADR-0052 §5).
WINDOW_BACK_DAYS = 14
WINDOW_FORWARD_DAYS = 400

#: Page cap for one run's remote listing (each page is already capped
#: by the transport's DEFAULT_PAGE_SIZE).
PAGE_CAP = 5

#: A binding stops retrying after this many consecutive failures
#: (bounded backoff — never grind forever).
FAILURE_CAP = 5

#: Sync-run retryable codes (transient ⇒ degraded → failing).
_RETRYABLE_CODES = ("unreachable", "rate-limited", "provider", "io")

#: Terminal codes: a typed STOP (ADR-0052 §3 failure posture). The user or
#: the deployment must act — retrying adds nothing, so these neither count
#: toward the failure cap nor re-notify on every run (notify **once**).
_TERMINAL_CODES = (
    "auth",  # provider rejected the grant → needs-auth
    "needs-secret",  # no usable secret → needs-secret
    "not-ready",
    "invalid",
    "unsupported",
    "net-denied",  # the SSRF guard refused the URL → config change required
)

#: The inbound default: only health's own projections come back (the
#: round-trip). A personal calendar is NEVER pulled wholesale — accepting
#: foreign events is an explicit link option (``acceptCategories`` /
#: ``acceptAll``), mirroring study.
_HEALTH_UID_PREFIX = "health:"

#: Charter codes a run may fail with (typed at the seams). Anything outside
#: this set collapses to ``io`` so the audit vocabulary stays closed.
_KNOWN_FAILURE_CODES = (
    "auth",
    "needs-secret",
    "not-ready",
    "invalid",
    "unsupported",
    "net-denied",
    "unreachable",
    "rate-limited",
    "provider",
    "protocol",
    "not-found",
    "path-denied",
)

SYNC_LOCK_TTL = 600  # seconds


@dataclass
class SyncOutcome:
    """The caller-facing summary of one run (no event data)."""

    binding_id: Any
    ok: bool
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    skipped: int = 0
    error_code: str | None = None
    error_detail: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": str(self.binding_id),
            "ok": self.ok,
            "status": self.status,
            "counts": dict(self.counts),
            "skipped": self.skipped,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


# ---------------------------------------------------------------------------
# Lock (one per binding; degrade-open without Redis)
# ---------------------------------------------------------------------------


async def _try_acquire_lock(binding_id: Any) -> tuple[bool, str]:
    lock_key = f"calendar_sync_lock:{binding_id}"
    try:
        from app.core.redis import redis_client

        acquired = await redis_client.set(lock_key, "1", nx=True, ex=SYNC_LOCK_TTL)
        if acquired is None:
            acquired = False
        return bool(acquired), lock_key
    except Exception:
        logger.warning(
            "Redis unavailable for calendar sync lock on %s — proceeding without "
            "dedup (duplicate writes possible under overlapping runs)",
            binding_id,
        )
        return True, lock_key


async def _release_lock(lock_key: str) -> None:
    try:
        from app.core.redis import redis_client

        await redis_client.delete(lock_key)
    except Exception:
        pass  # TTL is the crash-recovery fallback


# ---------------------------------------------------------------------------
# Transport + auth
# ---------------------------------------------------------------------------


class _NoSecretError(RuntimeError):
    """Raised by the auth provider when no usable access token exists."""


class _GrantRejected(CalendarCredentialError):
    """The provider rejected the stored grant (``invalid_grant``/revoked).

    Charter ``auth`` → ``needs-auth``: stop, notify once, the user
    reconnects (ADR-0052 §3 failure posture).
    """


def _options(binding: Any) -> dict[str, Any]:
    return dict(binding.options or {})


def _oauth_access_token(secret: dict[str, Any]) -> str:
    """The access token from an oauth slot; refuses a known-expired one."""
    expires_raw = secret.get("expires_at")
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(str(expires_raw))
        except ValueError:
            expires_at = None
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if datetime.now(UTC) >= expires_at:
                raise _NoSecretError("The stored access token is expired.")
    token = secret.get("access_token")
    if not token:
        raise _NoSecretError("No access token stored.")
    return str(token)


async def _refresh_oauth_token(
    db: AsyncSession, binding: Any, secret: dict[str, Any], http: httpx.AsyncClient
) -> dict[str, Any]:
    """Refresh-on-use for oauth2 bindings (never call with a dead token).

    Uses the catalog token URL (data) + the client id stored in the slot.
    ``invalid_grant``/revocation surfaces as charter ``auth`` — the
    caller maps it to ``needs-auth``.
    """
    from app.extensions.sdk.auth import refresh_token as sdk_refresh_token

    provider = resolve_provider(default_catalog(), binding.provider, binding.service)
    oauth = provider.oauth
    token_url = secret.get("token_endpoint") or (oauth.token_url if oauth else None)
    client_id = secret.get("client_id")
    refresh_value = secret.get("refresh_token")
    if not token_url or not client_id or not refresh_value:
        raise binding_credentials.CalendarCredentialError(
            "The OAuth slot lacks a refreshable grant — reconnect the source.",
            code="needs-secret",
        )
    token = await sdk_refresh_token(
        token_url,
        refresh_value,
        client_id,
        client_secret=oauth_clients.client_secret_for(binding.provider),
        http=http,
    )
    normalized = {
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token") or refresh_value,
        "expires_at": token.get("expires_at"),
        "scope": token.get("scope") or secret.get("scope"),
        "token_endpoint": token_url,
        "client_id": client_id,
    }
    await binding_credentials.set_secret(
        db,
        binding,
        normalized,
        scopes=_scope_list(normalized.get("scope")),
        access_expires_at=parse_expires(normalized.get("expires_at")),
    )
    return normalized


def _scope_list(scope: Any) -> list[str]:
    if isinstance(scope, str):
        return [item for item in scope.replace(",", " ").split() if item]
    if isinstance(scope, (list, tuple)):
        return [str(item) for item in scope]
    return []


def parse_expires(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _auth_provider(
    db: AsyncSession, binding: Any, secret: dict[str, Any], http: httpx.AsyncClient
):
    """Build the per-call auth header provider for the transport.

    The refresh path implements ADR-0052 §3's failure posture:

    * ``invalid_grant`` / revoked / expired refresh ⇒ :class:`_GrantRejected`
      (charter ``auth``) → ``needs-auth`` — stop, notify once;
    * ``403 insufficient_scope`` ⇒ ``needs-secret`` — a fresh consent screen;
    * transient refresh failures keep the charter code (``unreachable`` /
      ``provider`` / ``rate-limited``) ⇒ ``degraded`` → ``failing`` with
      bounded retries.
    """
    if binding.auth_kind == "oauth2":
        try:
            token = _oauth_access_token(secret)
        except _NoSecretError:
            try:
                secret = await _refresh_oauth_token(db, binding, secret, http)
            except ExtensionError as exc:
                if exc.code == "auth":
                    if getattr(exc, "status", None) == 403:
                        # RFC 6749 §5.2/§4.1.2.1: the grant is valid but the
                        # granted scopes no longer cover the ask — re-consent.
                        raise CalendarCredentialError(
                            "The provider rejected the granted scopes "
                            "(insufficient scope) — reconnect to grant them.",
                            code="needs-secret",
                        ) from exc
                    raise _GrantRejected(
                        "The provider rejected the stored grant (revoked or "
                        "expired) — reconnect the account."
                    ) from exc
                raise  # transient: unreachable / provider / rate-limited
            token = str(secret["access_token"])
        return lambda: bearer_auth(token)

    if binding.auth_kind == "app-password":
        username = str(secret.get("username") or binding.account_label or "")
        password = str(secret.get("password") or "")
        return lambda: basic_auth(username, password)

    if binding.auth_kind == "token":
        token = str(secret.get("token") or "")
        # A private ICS feed authenticates through its URL query, not a
        # header — a url-only slot sends no Authorization header.
        if not token and secret.get("url"):
            return None
        return lambda: bearer_auth(token)

    return None


def _transport_url(options: dict[str, Any], secret: dict[str, Any], key: str) -> str | None:
    """The address for a URL-keyed protocol option, honoring C4 custody.

    The row keeps only scheme+host+path; when a query string made the URL a
    secret, the full address is rehydrated from the sealed slot.
    """
    sanitized = options.get(key) or None
    sealed = secret.get(key) or None
    if sealed and isinstance(sealed, str):
        from urllib.parse import urlsplit

        # The sealed full URL wins when the row address is its query-less
        # form (same host + path); a divergent row value stands alone.
        if not sanitized:
            return sealed
        sealed_parts, row_parts = urlsplit(str(sealed)), urlsplit(str(sanitized))
        same_address = (
            sealed_parts.hostname == row_parts.hostname
            and (sealed_parts.path or "/") == (row_parts.path or "/")
        )
        if same_address:
            return sealed
    return str(sanitized) if sanitized else None


def build_transport(
    binding: Any,
    http: httpx.AsyncClient,
    auth,
    *,
    secret: dict[str, Any] | None = None,
    guard: bool = True,
) -> CalendarTransport:
    """Build the catalog-keyed transport for one binding.

    The client seam is the guarded one (:class:`GuardedCalendarClient`) — a
    self-hosted CalDAV/ICS URL is a user-supplied URL, i.e. a server-side
    fetch primitive; every request is SSRF-guarded, redirect-reguarded and
    size-capped. ``guard=False`` exists only for tests that inject their own
    transport seam.
    """
    provider = resolve_provider(default_catalog(), binding.provider, binding.service)
    spec = provider.service(binding.service)
    options = _options(binding)
    sealed = secret or {}
    client = wrap_guarded(http) if guard else http
    return transport_for(
        spec.protocol,
        client,
        auth=auth,
        calendar_id=str(options.get("calendar_id") or ""),
        calendar_url=_transport_url(options, sealed, "calendar_url"),
        url=_transport_url(options, sealed, "url"),
    )


# ---------------------------------------------------------------------------
# Local records / links
# ---------------------------------------------------------------------------


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _window(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=WINDOW_BACK_DAYS)).astimezone(UTC)
    end = (now + timedelta(days=WINDOW_FORWARD_DAYS)).astimezone(UTC)
    return start, end


async def _load_projections(
    db: AsyncSession, binding: Any, now: datetime
) -> list[Projection]:
    """Load this binding's patient records and map them (host-owned mapping)."""
    window_start, window_end = _window(now)
    day_start: date = (window_start - timedelta(days=1)).date()
    day_end: date = (window_end + timedelta(days=1)).date()
    projections: list[Projection] = []

    exams = (
        await db.execute(
            select(ExaminationModel)
            .where(
                ExaminationModel.tenant_id == binding.tenant_id,
                ExaminationModel.patient_id == binding.patient_id,
                ExaminationModel.deleted_at.is_(None),
                ExaminationModel.examination_date.isnot(None),
                ExaminationModel.examination_date >= day_start,
                ExaminationModel.examination_date <= day_end,
            )
        )
    ).scalars().all()
    for exam in exams:
        organization_name = None
        if "organization" in exam.__dict__ and exam.organization is not None:
            organization_name = exam.organization.name
        category_name = None
        if "category_concept" in exam.__dict__ and exam.category_concept is not None:
            category_name = exam.category_concept.name
        mapped = examination_event(
            exam,
            redaction=binding.redaction,
            category_name=category_name,
            organization_name=organization_name,
        )
        if mapped is not None:
            projections.append(mapped)

    events = (
        await db.execute(
            select(ClinicalEvent)
            .where(
                ClinicalEvent.tenant_id == binding.tenant_id,
                ClinicalEvent.patient_id == binding.patient_id,
                ClinicalEvent.deleted_at.is_(None),
                ClinicalEvent.onset_date.isnot(None),
                ClinicalEvent.onset_date >= window_start,
                ClinicalEvent.onset_date <= window_end,
                # Inbound (calendar-origin) records are NOT projections:
                # they entered from a provider and are unverified (C3) —
                # pushing them back out under a health: uid would round-trip
                # untrusted text into the calendar.
                or_(
                    ClinicalEvent.event_metadata["origin"].astext.is_(None),
                    ClinicalEvent.event_metadata["origin"].astext != "calendar",
                ),
            )
        )
    ).scalars().all()
    for event_row in events:
        type_name = None
        if "type_entity" in event_row.__dict__ and event_row.type_entity is not None:
            type_name = event_row.type_entity.name
        mapped = clinical_event_event(event_row, redaction=binding.redaction, type_name=type_name)
        if mapped is not None:
            projections.append(mapped)

    projections.extend(await _load_pulled_projections(db, binding, window_start, window_end))
    projections.extend(
        await _load_occurrence_projections(db, binding, window_start, window_end)
    )

    return projections


async def _load_pulled_projections(
    db: AsyncSession, binding: Any, window_start: datetime, window_end: datetime
) -> list[Projection]:
    """Inverse projections for calendar-origin records (C3).

    A pulled record projects back as a :class:`LocalEvent` under its
    **remote uid** (``external_id``), so reconcile sees local + remote +
    link as one pinned pair instead of reading the record as "local-new"
    (which would push untrusted inbound text back out) or as "local-missing"
    (which would unlink/recreate the link every run). The projection carries
    only what the pull stored — never remote-only fields.
    """
    rows = (
        await db.execute(
            select(ClinicalEvent)
            .where(
                ClinicalEvent.tenant_id == binding.tenant_id,
                ClinicalEvent.patient_id == binding.patient_id,
                ClinicalEvent.deleted_at.is_(None),
                ClinicalEvent.external_id.isnot(None),
                ClinicalEvent.source_integration_id.is_(None),
                ClinicalEvent.event_metadata["origin"].astext == "calendar",
                ClinicalEvent.onset_date >= window_start,
                ClinicalEvent.onset_date <= window_end,
            )
        )
    ).scalars().all()
    projections: list[Projection] = []
    for row in rows:
        event = _pulled_event(row)
        if event is not None:
            projections.append(
                Projection(local_kind="clinical_event", local_id=row.id, event=event)
            )
    return projections


def _pulled_event(row: Any) -> CalendarEvent | None:
    """Rebuild the local-side event for one calendar-origin record."""
    onset = _aware(row.onset_date)
    if onset is None:
        return None
    metadata = dict(row.event_metadata or {})
    if metadata.get("all_day"):
        start: date | datetime = onset.date()
        end: date | datetime | None = exclusive_end(start)
    else:
        start = onset
        end = _aware(row.resolved_date)
    return CalendarEvent(
        uid=str(row.external_id),
        start=start,
        end=end,
        all_day=bool(metadata.get("all_day")),
        summary=str(row.title or ""),
        description=str(row.description) if row.description else "",
    )


async def _load_occurrence_projections(
    db: AsyncSession, binding: Any, window_start: datetime, window_end: datetime
) -> list[Projection]:
    occ_rows = (
        await db.execute(
            select(ClinicalEventOccurrence)
            .join(ClinicalEvent, ClinicalEventOccurrence.event_id == ClinicalEvent.id)
            .where(
                ClinicalEvent.tenant_id == binding.tenant_id,
                ClinicalEvent.patient_id == binding.patient_id,
                ClinicalEvent.deleted_at.is_(None),
                ClinicalEventOccurrence.occurred_at >= window_start,
                ClinicalEventOccurrence.occurred_at <= window_end,
            )
        )
    ).scalars().all()
    projections = []
    for occurrence in occ_rows:
        mapped = occurrence_event(occurrence, redaction=binding.redaction)
        if mapped is not None:
            projections.append(mapped)
    return projections


async def _load_links(db: AsyncSession, binding: Any) -> list[EventLink]:
    rows = (
        await db.execute(
            select(CalendarEventLink).where(CalendarEventLink.binding_id == binding.id)
        )
    ).scalars().all()
    return [
        EventLink(
            local_id=str(row.local_id),
            uid=row.uid,
            remote_id=row.remote_id,
            etag=row.etag,
            local_hash=row.local_hash,
            remote_hash=row.remote_hash,
            updated_at=_aware(row.updated_at) or _now(),
        )
        for row in rows
    ]


def _accept_predicate(binding: Any):
    """The inbound acceptance predicate (ADR-0052 §5/§6, Rev 3 E11).

    A pulled event becomes a candidate ONLY when the binding opts in:

    * ``health:``-uid events (our own round-trip) are always accepted;
    * ``acceptCategories`` (link option, list of category strings,
      case-insensitive) admits matching foreign events — mirroring study's
      predicate;
    * ``acceptAll: true`` is the explicit opt-in for the whole feed.

    **Attributable-only** (ADR-0052 §6 / E8): the shared
    :func:`is_attributable` rule decides, so a remote event enters health's
    record only when it carries the family marker, its uid is one we minted,
    the binding opted into ``acceptAll``, or the user listed the categories
    themselves. A title that merely looks clinical is never a record — in
    health a false positive becomes a clinical entry. Keyword lookalikes are
    surfaced as *unverified suggestions* for the confirmation surface, never
    written silently.
    """
    options = _options(binding)
    accept_all = options.get("accept_all", options.get("acceptAll")) is True
    raw_categories = options.get("accept_categories", options.get("acceptCategories")) or ()
    allowed_categories = {str(value).strip().upper() for value in raw_categories}

    def accept(event: CalendarEvent) -> bool:
        if is_attributable(event, namespaces=(_HEALTH_UID_PREFIX,)):
            return True
        if accept_all:
            return True
        if not allowed_categories:
            return False
        event_categories = {str(value).strip().upper() for value in event.categories}
        return bool(allowed_categories & event_categories)

    return accept


def _local_events(projections: list[Projection]) -> list[LocalEvent]:
    return [
        LocalEvent(
            local_id=str(projection.local_id),
            event=projection.event,
            updated_at=_aware(projection.event.updated_at) or _now(),
        )
        for projection in projections
    ]


async def _list_remote(
    transport: CalendarTransport,
    window_start: datetime,
    window_end: datetime,
    sync_cursor: str | None = None,
) -> tuple[list[CalendarEvent], str | None]:
    """Full-window listing; returns (events, next sync cursor)."""
    events: list[CalendarEvent] = []
    next_sync_token: str | None = None
    page_token: str | None = None
    for _ in range(PAGE_CAP):
        page: CalendarPage = await transport.list_events(
            window_start, window_end, page_token=page_token
        )
        events.extend(page.events)
        next_sync_token = page.next_sync_token or next_sync_token
        page_token = page.next_page_token
        if not page_token:
            break
    return events, next_sync_token


async def _changed_since(
    transport: CalendarTransport, cursor: str
) -> tuple[bool, str | None]:
    """Has anything changed since ``cursor``? (M4 — the safe delta form.)

    A delta listing returns only CHANGED events, so it must never be used as
    the reconcile's remote view — that reads every unchanged event as
    "vanished" and unlinks it. Used purely as a fast path: no changes -> skip
    the reconcile; any change (including a tombstone) -> full window; a dead
    cursor -> dropped, never a failure.
    """
    try:
        page = await transport.list_events(
            datetime.now(UTC), datetime.now(UTC), sync_token=cursor
        )
    except CalendarError:
        return True, None
    changed = bool(page.events) or page.next_page_token is not None
    return changed, page.next_sync_token


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


async def _upsert_link(
    db: AsyncSession,
    binding: Any,
    *,
    local_id: str,
    uid: str,
    remote_id: str | None,
    etag: str | None,
    local_hash: str | None,
    remote_hash: str | None,
    now: datetime,
) -> None:
    """Pin the sync identity with SEPARATE per-side hashes (E3).

    Providers normalize on write; one shared hash would read the
    normalization back as a change and loop forever.
    """
    result = await db.execute(
        select(CalendarEventLink).where(
            CalendarEventLink.binding_id == binding.id, CalendarEventLink.uid == uid
        )
    )
    row = result.scalars().one_or_none()
    if row is None:
        row = CalendarEventLink(
            binding_id=binding.id,
            local_kind=_link_kind_for_uid(uid),
            local_id=UUID(local_id),
            uid=uid,
            remote_id=remote_id,
            etag=etag,
            local_hash=local_hash,
            remote_hash=remote_hash,
        )
        db.add(row)
    else:
        row.local_id = UUID(local_id)
        row.remote_id = remote_id
        row.etag = etag
        row.local_hash = local_hash
        row.remote_hash = remote_hash
        row.updated_at = now


def _link_kind_for_uid(uid: str) -> str:
    if uid.startswith("health:examination:"):
        return "examination"
    if uid.startswith("health:clinical-event:"):
        return "clinical_event"
    if uid.startswith("health:occurrence:"):
        return "occurrence"
    return "clinical_event"


async def _delete_link(db: AsyncSession, binding: Any, uid: str) -> None:
    result = await db.execute(
        select(CalendarEventLink).where(
            CalendarEventLink.binding_id == binding.id, CalendarEventLink.uid == uid
        )
    )
    row = result.scalars().one_or_none()
    if row is not None:
        await db.delete(row)


async def _create_local_event(
    db: AsyncSession, binding: Any, event: CalendarEvent
) -> ClinicalEvent:
    """An accepted remote event becomes a CLINICAL RECORD — untrusted (C3).

    Inbound calendar text is never silently promoted to verified clinical
    data (ADR-0052 §6 "inbound is untrusted", Rev 3 C3):

    * ``origin='calendar'`` + ``unverified: True`` in ``event_metadata`` —
      visibly flagged, never auto-completed (status stays ``ACTIVE``; no
      code path here may set ``RESOLVED``);
    * it enters the record only through the confirmation surface
      (``POST /calendar/bindings/{id}/inbound/{event_id}/confirm``);
    * dedup rides the ``uq_clinical_events_calendar_dedup`` partial unique
      index via ``external_id`` (the remote uid): the pre-check links an
      existing row instead of duplicating (crash-window idempotency), the
      index is the concurrent-race backstop.
    """
    existing = (
        await db.execute(
            select(ClinicalEvent).where(
                ClinicalEvent.tenant_id == binding.tenant_id,
                ClinicalEvent.patient_id == binding.patient_id,
                ClinicalEvent.source_integration_id.is_(None),
                ClinicalEvent.external_id == event.uid,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    all_day = not isinstance(event.start, datetime)
    if all_day:
        onset: datetime | None = datetime(
            event.start.year, event.start.month, event.start.day, tzinfo=UTC
        )
        resolved: datetime | None = None  # an all-day block has no end time
    else:
        onset = _aware(event.start)
        resolved = _aware(event.end) if isinstance(event.end, datetime) else None
    row = ClinicalEvent(
        tenant_id=binding.tenant_id,
        patient_id=binding.patient_id,
        status=ClinicalEventStatus.ACTIVE,  # never auto-completed
        title=(event.summary or NEUTRAL_SUMMARY)[:255],
        description=(event.description or "")[:2000] or None,
        onset_date=onset,
        resolved_date=resolved if resolved is not None and onset is not None else None,
        external_id=event.uid,
        event_metadata={
            "origin": "calendar",
            "calendar_binding_id": str(binding.id),
            "unverified": True,
            "all_day": all_day,
        },
    )
    db.add(row)
    await db.flush()
    return row


async def _apply_plan(
    db: AsyncSession,
    binding: Any,
    plan: SyncPlan,
    transport: CalendarTransport,
    projections_by_uid: dict[str, Projection],
    now: datetime,
) -> dict[str, int]:
    """Execute the plan transactionally; per-action counts, no event data.

    Hash discipline (E3, the study convention): each side is pinned to the
    hash of the content last seen on THAT side — after a remote write, the
    local pin is the projection we sent and the remote pin is the provider's
    echo; after a local apply, both pins are the remote event as applied.
    """
    counts: dict[str, int] = {}
    for action in plan.actions:
        counts[action.kind.value] = counts.get(action.kind.value, 0) + 1
        if action.kind in (ActionKind.CREATE_REMOTE, ActionKind.UPDATE_REMOTE):
            assert action.event is not None  # reconcile always carries it
            written = await transport.put_event(action.event)
            projection = projections_by_uid.get(action.uid)
            local_id = (
                str(projection.local_id) if projection is not None else str(action.local_id)
            )
            await _upsert_link(
                db,
                binding,
                local_id=local_id,
                uid=action.uid,
                remote_id=written.remote_id,
                etag=written.etag,
                local_hash=event_hash(action.event),
                remote_hash=event_hash(written),
                now=now,
            )
        elif action.kind == ActionKind.DELETE_REMOTE:
            assert action.event is not None
            await transport.delete_event(action.event)
            await _delete_link(db, binding, action.uid)
        elif action.kind == ActionKind.UNLINK:
            await _delete_link(db, binding, action.uid)
        elif action.kind == ActionKind.LINK_EXISTING:
            # E9 orphan repair: the remote already exists (the link write was
            # lost between the provider write and our commit) — attach it to
            # the local record instead of creating a duplicate event.
            assert action.event is not None
            await _upsert_link(
                db,
                binding,
                local_id=action.local_id or action.uid,
                uid=action.uid,
                remote_id=action.event.remote_id,
                etag=action.event.etag,
                local_hash=None,
                remote_hash=event_hash(action.event),
                now=now,
            )
            counts["linked-existing"] = counts.get("linked-existing", 0) + 1
        elif action.kind == ActionKind.CREATE_LOCAL:
            assert action.event is not None
            local_row = await _create_local_event(db, binding, action.event)
            local_projection = _pulled_event(local_row) or action.event
            await _upsert_link(
                db,
                binding,
                local_id=str(local_row.id),
                uid=action.uid,
                remote_id=action.event.remote_id,
                etag=action.event.etag,
                local_hash=event_hash(local_projection),
                remote_hash=event_hash(action.event),
                now=now,
            )
        elif action.kind == ActionKind.UPDATE_LOCAL:
            # Only sync-owned records follow the remote: a record the user
            # created by hand keeps its fields (skipped, counted).
            assert action.event is not None
            local_id = action.local_id
            if local_id is None:
                counts["skipped-locally-owned"] = (
                    counts.get("skipped-locally-owned", 0) + 1
                )
                continue
            row = (
                await db.execute(
                    select(ClinicalEvent).where(
                        ClinicalEvent.id == UUID(str(local_id)),
                        ClinicalEvent.external_id == action.uid,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                counts["skipped-locally-owned"] = counts.get("skipped-locally-owned", 0) + 1
                continue
            if isinstance(action.event.start, datetime):
                row.onset_date = _aware(action.event.start)
                row.resolved_date = (
                    _aware(action.event.end)
                    if isinstance(action.event.end, datetime)
                    else row.resolved_date
                )
            if action.event.summary:
                row.title = action.event.summary[:255]
            row.description = action.event.description or row.description
            await db.flush()
            local_projection = _pulled_event(row) or action.event
            await _upsert_link(
                db,
                binding,
                local_id=str(row.id),
                uid=action.uid,
                remote_id=action.event.remote_id,
                etag=action.event.etag,
                local_hash=event_hash(local_projection),
                remote_hash=event_hash(action.event),
                now=now,
            )
        elif action.kind == ActionKind.CONFLICT:
            counts["conflicts"] = counts.get("conflicts", 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Status transitions + audit
# ---------------------------------------------------------------------------


def _recovered_code(exc: CalendarError) -> str:
    """The typed code of a failed run.

    The vendored transport re-wraps every client error as ``unreachable``
    (``except Exception`` at its seam) — a host-guard ``CalendarError``
    (``net-denied`` from the SSRF guard) survives in the detail prefix, so
    the host recovers the original code instead of misfiling a config
    refusal as a transient network error.
    """
    if exc.code == "unreachable" and exc.detail.startswith("net-denied:"):
        return "net-denied"
    return exc.code


def _failure_status(code: str, consecutive_failures: int) -> str:
    """The failure posture (ADR-0052 §3/§8): typed stop, bounded retries.

    ``auth`` (provider rejected the grant) ⇒ ``needs-auth``; missing or
    unusable credentials and scope refusals ⇒ ``needs-secret``; the SSRF
    guard's ``net-denied`` is a config error (the remedy is the URL/host
    setting, never a retry) ⇒ ``failing``; transient codes ⇒ ``degraded``
    until the failure cap, then ``failing``.
    """
    if code == "auth":
        return "needs-auth"
    if code in ("needs-secret", "not-ready", "invalid", "unsupported"):
        return "needs-secret"
    if code == "net-denied":
        return "failing"
    if code in _RETRYABLE_CODES and consecutive_failures >= FAILURE_CAP:
        return "failing"
    return "degraded"


def _cap_error_detail(detail: str | None) -> str | None:
    """Scrub + cap an error detail before it is stored or surfaced.

    The scrub is the C4/E12 boundary: provider errors (vendored transports
    embed ``str(exc)``) can carry the full request URL — for a private ICS
    feed that query string is a bearer token. No URL query, no userinfo,
    past this point (into ``last_error``, audit rows or responses).
    """
    if not detail:
        return None
    return sanitize_error_detail(detail)[:300]


async def _restore_identity(db: AsyncSession, binding: Any) -> None:
    """Re-load the binding's identity after a rollback expired the object.

    A failed sync path must still write typed rows (failure audit + status),
    but the rollback expires every ORM attribute and async lazy IO is
    illegal outside a greenlet — the same helper the connector engine's
    ``run_sync`` needs after a rollback.
    """
    with contextlib.suppress(Exception):
        await db.refresh(binding)


async def _record_failure(
    db: AsyncSession,
    binding: Any,
    *,
    code: str,
    detail: str | None,
    now: datetime,
    started_at: datetime,
    source: str,
) -> SyncOutcome:
    """Record one failed run: status transition + one audit row.

    Posture law (ADR-0052 §3/§8): a **terminal** code (auth, needs-secret,
    net-denied, …) is a typed STOP — it never counts toward the transient
    failure cap and, when the binding already sits in the target state, it
    neither re-writes the audit row nor re-notifies (notify **once**). A
    transient code counts toward the cap (``degraded`` → ``failing``).
    ``detail`` is scrubbed + capped — no URL query, no token material.
    """
    failures = int(binding.consecutive_failures or 0)
    target = _failure_status(code, failures)
    if code in _TERMINAL_CODES:
        if binding.status == target:
            return SyncOutcome(
                binding_id=binding.id,
                ok=False,
                status=target,
                error_code=code,
                error_detail=_cap_error_detail(detail),
                started_at=started_at,
                completed_at=now,
            )
    else:
        failures += 1
        binding.consecutive_failures = failures
        target = _failure_status(code, failures)
    binding.status = target
    binding.last_error = f"{code}: {_cap_error_detail(detail) or 'sync failed'}"
    log_row = CalendarSyncLog(
        tenant_id=binding.tenant_id,
        binding_id=binding.id,
        status="failed",
        counts={},
        skipped=0,
        error_code=code[:24],
        error_detail=_cap_error_detail(detail),
        source=source[:16],
        started_at=started_at,
        completed_at=now,
    )
    db.add(log_row)
    await db.commit()
    return SyncOutcome(
        binding_id=binding.id,
        ok=False,
        status=binding.status,
        error_code=code,
        error_detail=_cap_error_detail(detail),
        started_at=started_at,
        completed_at=now,
    )


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


async def run_sync(
    db: AsyncSession,
    binding: Any,
    *,
    transport: CalendarTransport | None = None,
    http: httpx.AsyncClient | None = None,
    now: datetime | None = None,
    source: str = "manual",
) -> SyncOutcome:
    """Run one bounded sync for a binding and commit the outcome.

    ``transport``/``http`` are injectable (tests pass fakes); when absent
    a short-lived client is created and closed. ``source`` is the audit
    row's run origin (``manual`` | ``beat``).
    """
    started = _now() if now is None else now
    acquired, lock_key = await _try_acquire_lock(binding.id)
    if not acquired:
        return SyncOutcome(
            binding_id=binding.id,
            ok=True,
            status="skipped",
            started_at=started,
            completed_at=_now(),
        )

    owned_http = None
    if http is None:
        owned_http = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        http = owned_http

    try:
        if not binding.enabled:
            return SyncOutcome(
                binding_id=binding.id,
                ok=True,
                status="skipped",
                started_at=started,
                completed_at=_now(),
            )

        now = _now() if now is None else now
        # ---- credential gate (fail closed, typed) ----
        try:
            secret = await binding_credentials.require_secret(db, binding)
        except binding_credentials.CalendarCredentialError as exc:
            await db.rollback()
            await _restore_identity(db, binding)
            return await _record_failure(
                db,
                binding,
                code=exc.code,
                detail=exc.detail,
                now=_now(),
                started_at=started,
                source=source,
            )

        # ---- transport (the guarded client seam; URLs rehydrated from the
        # sealed slot so a private feed's full URL never lives in the row) ----
        try:
            auth = await _auth_provider(db, binding, secret, http)
        except binding_credentials.CalendarCredentialError as exc:
            await db.rollback()
            await _restore_identity(db, binding)
            return await _record_failure(
                db,
                binding,
                code=exc.code,
                detail=exc.detail,
                now=_now(),
                started_at=started,
                source=source,
            )
        if transport is None:
            transport = build_transport(binding, http, auth, secret=secret)

        # ---- window + local/links ----
        window_start, window_end = _window(now)
        projections = await _load_projections(db, binding, now)
        projections_by_uid = {p.event.uid: p for p in projections}
        links = await _load_links(db, binding)

        # ---- M4 fast path: nothing changed since the last run -> done ----
        sync_cursor = binding.sync_cursor
        if sync_cursor:
            changed, fresh_cursor = await _changed_since(transport, sync_cursor)
            if not changed:
                if fresh_cursor:
                    binding.sync_cursor = fresh_cursor[:1024]
                binding.last_synced_at = _now()
                binding.consecutive_failures = 0
                binding.last_error = None
                binding.status = "ok"
                await db.commit()
                return SyncOutcome(
                    binding_id=binding.id,
                    ok=True,
                    status="skipped",
                    started_at=started,
                    completed_at=_now(),
                )

        # ---- remote listing (cursor round-trips; stale token → full resync) ----
        try:
            remote_events, next_sync_token = await _list_remote(
                transport, window_start, window_end
            )
        except CalendarError as exc:
            # `not-ready` is the contract's code for a dead cursor (E13).
            if exc.code in ("not-ready", "stale-token") and sync_cursor:
                binding.sync_cursor = None
                remote_events, next_sync_token = await _list_remote(
                    transport, window_start, window_end
                )
            else:
                await db.rollback()
                await _restore_identity(db, binding)
                return await _record_failure(
                    db,
                    binding,
                    code=_recovered_code(exc),
                    detail=exc.detail,
                    now=_now(),
                    started_at=started,
                    source=source,
                )

        # ---- pure reconcile ----
        direction = SyncDirection(binding.direction)
        provider = resolve_provider(default_catalog(), binding.provider, binding.service)
        remote_writable = (
            provider.service(binding.service).writable and direction != SyncDirection.PULL
        )
        options = _options(binding)
        plan = reconcile(
            _local_events(projections),
            remote_events,
            links,
            direction=direction,
            # C2: the default is skip (surface the conflict) — never
            # clock-based newest-wins across unsynchronized clocks.
            conflicts=ConflictPolicy(str(options.get("conflict_policy") or "skip")),
            deletions=DeletionPolicy(str(options.get("deletion_policy") or "keep-local")),
            accept=_accept_predicate(binding),
            remote_writable=remote_writable,
            now=now,
            # E5: the remote list is window-bounded — without the window an
            # out-of-window link reads as "remote deleted" and unlinks.
            window=(window_start, window_end),
        )

        # ---- transactional apply ----
        try:
            counts = await _apply_plan(db, binding, plan, transport, projections_by_uid, now)
        except CalendarError as exc:
            await db.rollback()
            await _restore_identity(db, binding)
            return await _record_failure(
                db,
                binding,
                code=_recovered_code(exc),
                detail=exc.detail,
                now=_now(),
                started_at=started,
                source=source,
            )

        # ---- bookkeeping ----
        binding.status = "ok"
        binding.consecutive_failures = 0
        binding.last_error = None
        binding.last_synced_at = now
        binding.sync_cursor = next_sync_token
        db.add(
            CalendarSyncLog(
                tenant_id=binding.tenant_id,
                binding_id=binding.id,
                status="ok",
                counts=counts,
                skipped=plan.skipped,
                source=source[:16],
                started_at=started,
                completed_at=now,
            )
        )
        await db.commit()
        return SyncOutcome(
            binding_id=binding.id,
            ok=True,
            status="ok",
            counts=counts,
            skipped=plan.skipped,
            started_at=started,
            completed_at=now,
        )

    except binding_credentials.CalendarCredentialError as exc:
        await db.rollback()
        await _restore_identity(db, binding)
        return await _record_failure(
            db,
            binding,
            code=exc.code,
            detail=exc.detail,
            now=_now(),
            started_at=started,
            source=source,
        )
    except ExtensionError as exc:
        # The polite runtime's typed errors keep their charter code, so a
        # transient refresh failure counts toward the bounded retry cap
        # instead of being misfiled as "io".
        await db.rollback()
        await _restore_identity(db, binding)
        return await _record_failure(
            db,
            binding,
            code=exc.code if exc.code in _KNOWN_FAILURE_CODES else "io",
            detail=exc.detail,
            now=_now(),
            started_at=started,
            source=source,
        )
    except Exception as exc:
        await db.rollback()
        await _restore_identity(db, binding)
        # Scrubbed: an exception message can embed a full URL (its query
        # string may be a feed token) — never into the log.
        logger.error(
            "Calendar sync failed for binding %s: %s",
            binding.id,
            sanitize_error_detail(str(exc)),
        )
        return await _record_failure(
            db,
            binding,
            code="io",
            detail=str(exc),
            now=_now(),
            started_at=started,
            source=source,
        )
    finally:
        await _release_lock(lock_key)
        if owned_http is not None:
            with contextlib.suppress(Exception):
                await owned_http.aclose()


async def test_binding(
    db: AsyncSession,
    binding: Any,
    *,
    transport: CalendarTransport | None = None,
    http: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Connectivity probe: a small bounded list, no writes, no link rows."""
    owned_http = None
    if http is None:
        owned_http = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        http = owned_http
    try:
        secret = await binding_credentials.require_secret(db, binding)
        auth = await _auth_provider(db, binding, secret, http)
        if transport is None:
            transport = build_transport(binding, http, auth, secret=secret)
        now = _now()
        await _list_remote(
            transport, now - timedelta(days=7), now + timedelta(days=30), None
        )
        binding.status = "ok"
        binding.last_error = None
        binding.consecutive_failures = 0
        await db.commit()
        return {"ok": True, "status": "ok"}
    except binding_credentials.CalendarCredentialError as exc:
        await db.rollback()
        await _restore_identity(db, binding)
        binding.status = _failure_status(exc.code, int(binding.consecutive_failures or 0))
        binding.last_error = f"{exc.code}: {_cap_error_detail(exc.detail)}"
        await db.commit()
        return {"ok": False, "status": binding.status, "code": exc.code}
    except CalendarError as exc:
        await db.rollback()
        await _restore_identity(db, binding)
        code = _recovered_code(exc)
        binding.status = _failure_status(code, int(binding.consecutive_failures or 0))
        binding.last_error = f"{code}: {_cap_error_detail(exc.detail)}"
        if code not in _TERMINAL_CODES:
            binding.consecutive_failures = int(binding.consecutive_failures or 0) + 1
        await db.commit()
        return {"ok": False, "status": binding.status, "code": code}
    finally:
        if owned_http is not None:
            with contextlib.suppress(Exception):
                await owned_http.aclose()


__all__ = [
    "FAILURE_CAP",
    "PAGE_CAP",
    "SYNC_LOCK_TTL",
    "WINDOW_BACK_DAYS",
    "WINDOW_FORWARD_DAYS",
    "SyncOutcome",
    "build_transport",
    "parse_expires",
    "run_sync",
    "test_binding",
]

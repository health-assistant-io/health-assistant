"""Host-owned calendar mapping for health (ADR-0052 §6, plan 52 P2).

Projects health records into the family :class:`CalendarEvent` shape and
nothing else — the shared engine never decides what an examination means.

Uid law (stable identity across syncs):

* ``health:examination:<id>`` — all-day event on ``examination_date`` with
  the canonical **exclusive** end (``date + 1 day``);
* ``health:clinical-event:<id>`` — timed event when ``onset_date`` and
  ``resolved_date`` both carry times (``resolved > onset``), all-day on the
  onset day otherwise; a record without an onset has no projection (skip);
* ``health:occurrence:<id>`` — an occurrence is a point in time
  (``occurred_at``); it maps to an all-day block on that day. No duration
  is invented.

Redaction profiles (the privacy ladder, applied per calendar link):

* ``none`` — publish nothing but the time block: summary is the family
  redacted string ("Busy"), every other field empty, marked private.
  Honest definition: even the *fact* that the item is a medical
  appointment is withheld.
* ``neutral`` (default) — summary is the neutral string "Medical
  appointment"; no location, no description, no category detail. The
  calendar shows *that something* is scheduled, never *what*.
* ``full`` — consent-gated opt-in: the real user-visible title, the
  organization name as location, the record's notes as description, the
  clinical-event type as category. Setting this profile through the API
  requires an explicit consent acknowledgement.

Granularity (Rev 3 E11 — "redaction is per link"): the profile lives on
the calendar **link** — health's binding row (``calendar_bindings.
redaction``), never on the provider/catalog row. The binding is the link
between this patient's record and one remote calendar, so per-link means
per-binding here; a per-record override would be a separate consented
refinement, never a wider default. ``full`` is consent-gated at the API
boundary on every request that sets it.

Never included under any profile: diagnoses, doctors, documents, patient
identifiers, or any field outside the explicit whitelists below. The
functions read whitelisted attributes only, so a leak is a code change,
not an accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.extensions.cloud.cloud_calendar import (
    REDACTED_SUMMARY,
    SOURCE_CATEGORY,
    CalendarEvent,
    event_hash,
)

UTC = UTC

#: The neutral summary (ADR-0052 §6: "Medical appointment").
NEUTRAL_SUMMARY = "Medical appointment"

#: The ``none`` profile's summary — the family's bare-block string.
NONE_SUMMARY = REDACTED_SUMMARY

UID_PREFIXES = {
    "examination": "health:examination:",
    "clinical_event": "health:clinical-event:",
    "occurrence": "health:occurrence:",
}


def event_uid(local_kind: str, local_id: UUID | str) -> str:
    """The stable projection uid for a local record."""
    try:
        prefix = UID_PREFIXES[local_kind]
    except KeyError as exc:
        raise ValueError(f"unknown local kind {local_kind!r}") from exc
    return f"{prefix}{local_id}"


def exclusive_end(day: date) -> date:
    """The canonical all-day end: exclusive (``day + 1``)."""
    return day + timedelta(days=1)


@dataclass(frozen=True)
class Projection:
    """One mapped record: the event plus its local coordinates."""

    local_kind: str
    local_id: UUID
    event: CalendarEvent


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _text(value: Any) -> str:
    return str(value) if value else ""


def _redact(
    *,
    local_kind: str,
    local_id: UUID,
    start: date | datetime,
    end: date | datetime | None,
    all_day: bool,
    redaction: str,
    title: str,
    location: str,
    description: str,
    category: str,
    updated_at: datetime | None,
) -> CalendarEvent:
    """Build one event through the redaction ladder (the only exit path)."""
    if redaction not in ("none", "neutral", "full"):
        raise ValueError(f"unknown redaction profile {redaction!r}")
    # E8: the family marker rides EVERY projected event — it is attribution,
    # not detail, so it is present at every redaction level. Without it the
    # round trip cannot tell our own events from a stranger's.
    if redaction == "full":
        summary, loc, desc = title, location, description
        categories = (SOURCE_CATEGORY, category) if category else (SOURCE_CATEGORY,)
    elif redaction == "neutral":
        summary, loc, desc = NEUTRAL_SUMMARY, "", ""
        categories = (SOURCE_CATEGORY,)
    else:
        summary, loc, desc = NONE_SUMMARY, "", ""
        categories = (SOURCE_CATEGORY,)
    return CalendarEvent(
        uid=event_uid(local_kind, local_id),
        start=start,
        end=end,
        all_day=all_day,
        summary=summary,
        description=desc,
        location=loc,
        categories=categories,
        private=redaction != "full",
        updated_at=_aware(updated_at),
    )


def examination_event(
    examination: Any,
    *,
    redaction: str = "neutral",
    category_name: str | None = None,
    organization_name: str | None = None,
) -> Projection | None:
    """Project an examination to an all-day event (exclusive end).

    ``examination`` needs ``id``, ``examination_date`` (a date), optional
    ``notes``/``patient_notes`` and ``updated_at`` — an ORM row or a
    namespace. ``category_name``/``organization_name`` are the caller's
    resolved display strings (never the whole related rows). Records
    without a date have no projection (``None``).
    """
    day: date | None = examination.examination_date
    if day is None:
        return None
    title = (category_name or "").strip() or NEUTRAL_SUMMARY
    event = _redact(
        local_kind="examination",
        local_id=examination.id,
        start=day,
        end=exclusive_end(day),
        all_day=True,
        redaction=redaction,
        title=title,
        location=_text(organization_name),
        description=_text(examination.notes) or _text(getattr(examination, "patient_notes", None)),
        category="examination",
        updated_at=getattr(examination, "updated_at", None),
    )
    return Projection(local_kind="examination", local_id=examination.id, event=event)


def clinical_event_event(
    event_row: Any,
    *,
    redaction: str = "neutral",
    type_name: str | None = None,
) -> Projection | None:
    """Project a clinical event: timed when onset/resolved carry times.

    ``onset_date``/``resolved_date`` are timezone-aware datetimes. With
    ``resolved > onset`` the projection is a timed event; any other shape
    (missing onset, missing resolved, ``resolved <= onset``) falls back to
    an all-day block on the onset day. A record without an onset has no
    projection (``None``).
    """
    onset = _aware(event_row.onset_date)
    if onset is None:
        return None
    resolved = _aware(event_row.resolved_date)
    if resolved is not None and resolved > onset:
        start: date | datetime = onset
        end: date | datetime | None = resolved
        all_day = False
    else:
        start = onset.date()
        end = exclusive_end(onset.date())
        all_day = True
    title = _text(event_row.title) or NEUTRAL_SUMMARY
    mapped = _redact(
        local_kind="clinical_event",
        local_id=event_row.id,
        start=start,
        end=end,
        all_day=all_day,
        redaction=redaction,
        title=title,
        location="",
        description=_text(getattr(event_row, "description", None)),
        category=_text(type_name),
        updated_at=getattr(event_row, "updated_at", None),
    )
    return Projection(local_kind="clinical_event", local_id=event_row.id, event=mapped)


def occurrence_event(occurrence: Any, *, redaction: str = "neutral") -> Projection | None:
    """Project one ``ClinicalEventOccurrence`` to an all-day block.

    An occurrence is a point (``occurred_at``); no duration is invented.
    """
    at = _aware(occurrence.occurred_at)
    if at is None:
        return None
    day = at.date()
    title = _text(occurrence.title) or NEUTRAL_SUMMARY
    mapped = _redact(
        local_kind="occurrence",
        local_id=occurrence.id,
        start=day,
        end=exclusive_end(day),
        all_day=True,
        redaction=redaction,
        title=title,
        location="",
        description=_text(getattr(occurrence, "notes", None)),
        category="occurrence",
        updated_at=getattr(occurrence, "updated_at", None),
    )
    return Projection(local_kind="occurrence", local_id=occurrence.id, event=mapped)


def projection_hash(projection: Projection) -> str:
    """The stable content hash of a projection (idempotency pin)."""
    return event_hash(projection.event)


__all__ = [
    "NEUTRAL_SUMMARY",
    "NONE_SUMMARY",
    "UID_PREFIXES",
    "Projection",
    "clinical_event_event",
    "event_uid",
    "examination_event",
    "exclusive_end",
    "occurrence_event",
    "projection_hash",
]

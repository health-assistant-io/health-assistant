"""Family cloud-calendar engine (ADR-0052).

Canonical copy lives in the family standards (``contracts/cloud_calendar.py``) and is
vendored byte-identical into product hosts via
``the family sync tooling``. Edit the canonical copy only.

The module is deliberately *domain-agnostic*. It knows how to:

* represent a calendar event in one normal form (:class:`CalendarEvent`);
* read and write iCalendar (RFC 5545) with a bounded recurrence expansion;
* talk to CalDAV, Google Calendar, Microsoft Graph and read-only ICS feeds
  behind one :class:`CalendarTransport` shape;
* reconcile a host's own records against a remote calendar as a **pure
  function** (:func:`reconcile`) returning the actions the host applies.

It knows nothing about studies, patients, deadlines or exams. Every host
maps its domain to and from ``CalendarEvent`` and keeps its own persistence,
consent UI and secret custody. Auth is injected per call (a header
provider), so no token ever enters this module's state.

Timezone rule: a timed event's ``start``/``end`` are timezone-aware
datetimes (UTC or a fixed offset); an all-day event uses ``datetime.date``
with ``all_day=True``. Floating local times are resolved by the parser with
the supplied ``default_timezone`` (UTC when absent) — never by the host's
machine clock, so tests and servers agree.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

#: Bounded recurrence expansion — an unbounded FREQ=DAILY rule must never
#: flood a host. Mirrors the family's "bounded expansion" rule.
MAX_OCCURRENCES = 750
MAX_RECURRENCE_DAYS = 366 * 5

#: Result caps for one list call (a host may page further).
DEFAULT_PAGE_SIZE = 250

CALENDAR_STATUSES = ("confirmed", "tentative", "cancelled")
TRANSPARENCIES = ("opaque", "transparent")

#: Safe default for hosts that project private items: never leak details.
REDACTED_SUMMARY = "Busy"

#: The marker category every projected event carries. It is BOTH the user's
#: filter handle ("show me my Neuronection events") and the family's
#: attribution signal (ADR-0052 §6 / E8): Google has no categories field, so
#: this rides `extendedProperties.private.nxCategories` there and a real
#: CATEGORIES field on CalDAV/Graph — and reads back as `categories` on all
#: three. Provider-agnostic on purpose: no host branches on a provider name.
SOURCE_CATEGORY = "NEURONECTION"


class CalendarError(Exception):
    """Typed calendar failure; ``code`` is stable across hosts."""

    def __init__(self, code: str, detail: str = "", *, retryable: bool = False) -> None:
        self.code = code
        self.detail = detail[:500]
        self.retryable = retryable
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


class SyncDirection(StrEnum):
    PUSH = "push"
    PULL = "pull"
    TWO_WAY = "two-way"


class ConflictPolicy(StrEnum):
    NEWEST_WINS = "newest-wins"
    LOCAL_WINS = "local-wins"
    REMOTE_WINS = "remote-wins"
    SKIP = "skip"


class DeletionPolicy(StrEnum):
    PROPAGATE = "propagate"
    KEEP_LOCAL = "keep-local"
    KEEP_REMOTE = "keep-remote"
    IGNORE = "ignore"


class ActionKind(StrEnum):
    CREATE_REMOTE = "create-remote"
    UPDATE_REMOTE = "update-remote"
    DELETE_REMOTE = "delete-remote"
    CREATE_LOCAL = "create-local"
    UPDATE_LOCAL = "update-local"
    DELETE_LOCAL = "delete-local"
    UNLINK = "unlink"
    CONFLICT = "conflict"
    #: Crash-window repair: an unlinked local record and an unlinked remote
    #: event are the SAME event (content match) — attach, never duplicate.
    LINK_EXISTING = "link-existing"


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    start: datetime | date
    summary: str = ""
    end: datetime | date | None = None
    all_day: bool = False
    description: str = ""
    location: str = ""
    status: str = "confirmed"
    transparency: str = "opaque"
    categories: tuple[str, ...] = ()
    sequence: int = 0
    url: str | None = None
    organizer: str | None = None
    remote_id: str | None = None
    etag: str | None = None
    updated_at: datetime | None = None
    recurrence: str | None = None
    recurrence_id: str | None = None
    alarms_minutes: tuple[int, ...] = ()
    private: bool = False

    def __post_init__(self) -> None:
        if not self.uid:
            raise CalendarError("invalid", "event uid is required")
        if self.all_day:
            if not isinstance(self.start, date) or isinstance(self.start, datetime):
                raise CalendarError("invalid", "all-day events need a date start")
            if self.end is not None and not isinstance(self.end, date):
                raise CalendarError("invalid", "all-day events need a date end")
        else:
            if isinstance(self.start, date) and not isinstance(self.start, datetime):
                raise CalendarError("invalid", "timed events need a datetime start")
            _require_aware(self.start)
            if self.end is not None:
                if isinstance(self.end, date) and not isinstance(self.end, datetime):
                    raise CalendarError("invalid", "timed events need a datetime end")
                _require_aware(self.end)
        if self.status not in CALENDAR_STATUSES:
            raise CalendarError("invalid", f"unknown status {self.status!r}")
        if self.transparency not in TRANSPARENCIES:
            raise CalendarError("invalid", f"unknown transparency {self.transparency!r}")

    @property
    def start_day(self) -> date:
        return self.start.date() if isinstance(self.start, datetime) else self.start

    @property
    def end_day(self) -> date:
        if self.end is None:
            return self.start_day
        return self.end.date() if isinstance(self.end, datetime) else self.end

    @property
    def duration_minutes(self) -> int:
        if self.all_day:
            days = max(0, (self.end_day - self.start_day).days) or 1
            return days * 24 * 60
        start = _as_datetime(self.start)
        end = _as_datetime(self.end) if self.end is not None else start + timedelta(hours=1)
        return max(0, int((end - start).total_seconds() // 60))

    def redacted(self, summary: str = REDACTED_SUMMARY) -> "CalendarEvent":
        """Privacy projection: keep the block, drop every identifying field."""
        return replace(
            self,
            summary=summary,
            description="",
            location="",
            categories=(),
            url=None,
            organizer=None,
            private=True,
        )

    def moved_to(self, start: datetime | date, end: datetime | date | None = None) -> "CalendarEvent":
        return replace(self, start=start, end=end)


def _require_aware(value: Any) -> None:
    if isinstance(value, datetime) and value.tzinfo is None:
        raise CalendarError("invalid", "timed datetimes must be timezone-aware")


def _as_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _day(value: date) -> str:
    return value.isoformat()


def _parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def _parse_dt(value: str, default_timezone: ZoneInfo | timezone = UTC) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CalendarError("invalid", f"unparseable datetime {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed


def event_to_dict(event: CalendarEvent) -> dict[str, Any]:
    return {
        "uid": event.uid,
        "summary": event.summary,
        "start": _day(event.start) if isinstance(event.start, date) and not isinstance(event.start, datetime) else _dt(event.start),
        "end": None
        if event.end is None
        else (
            _day(event.end)
            if isinstance(event.end, date) and not isinstance(event.end, datetime)
            else _dt(event.end)
        ),
        "allDay": event.all_day,
        "description": event.description,
        "location": event.location,
        "status": event.status,
        "transparency": event.transparency,
        "categories": list(event.categories),
        "sequence": event.sequence,
        "url": event.url,
        "organizer": event.organizer,
        "remoteId": event.remote_id,
        "etag": event.etag,
        "updatedAt": _dt(event.updated_at) if event.updated_at else None,
        "recurrence": event.recurrence,
        "recurrenceId": event.recurrence_id,
        "alarmsMinutes": list(event.alarms_minutes),
        "private": event.private,
    }


def event_from_dict(data: Mapping[str, Any], default_timezone: ZoneInfo | timezone = UTC) -> CalendarEvent:
    all_day = bool(data.get("allDay"))
    start_raw = data.get("start")
    end_raw = data.get("end")
    if start_raw is None:
        raise CalendarError("invalid", "event start is required")
    if all_day:
        start: datetime | date = _parse_day(str(start_raw))
        end: datetime | date | None = _parse_day(str(end_raw)) if end_raw else None
    else:
        start = _parse_dt(str(start_raw), default_timezone)
        end = _parse_dt(str(end_raw), default_timezone) if end_raw else None
    updated_raw = data.get("updatedAt")
    return CalendarEvent(
        uid=str(data["uid"]),
        start=start,
        end=end,
        summary=str(data.get("summary") or ""),
        all_day=all_day,
        description=str(data.get("description") or ""),
        location=str(data.get("location") or ""),
        status=str(data.get("status") or "confirmed"),
        transparency=str(data.get("transparency") or "opaque"),
        categories=tuple(str(item) for item in data.get("categories") or ()),
        sequence=int(data.get("sequence") or 0),
        url=data.get("url"),
        organizer=data.get("organizer"),
        remote_id=data.get("remoteId"),
        etag=data.get("etag"),
        updated_at=_parse_dt(str(updated_raw), default_timezone) if updated_raw else None,
        recurrence=data.get("recurrence"),
        recurrence_id=data.get("recurrenceId"),
        alarms_minutes=tuple(int(item) for item in data.get("alarmsMinutes") or ()),
        private=bool(data.get("private")),
    )


def event_content(event: CalendarEvent) -> dict[str, Any]:
    """The fields that count as a user-visible change.

    Identity and provider-echo fields are excluded: a provider that fills in
    ``htmlLink``/``organizer`` on write must NOT read back as a local change
    (the E3 normalization trap — that comparison is what makes a sync loop
    forever). ``url``, ``organizer`` and ``recurrenceId`` are observations.
    """
    payload = event_to_dict(event)
    for key in (
        "uid",
        "remoteId",
        "etag",
        "updatedAt",
        "sequence",
        "url",
        "organizer",
        "recurrenceId",
    ):
        payload.pop(key, None)
    return payload


def is_attributable(
    event: CalendarEvent, *, namespaces: Sequence[str] = ()
) -> bool:
    """Can this event be attributed to a family host?

    The inbound law (ADR-0052 §6 / E8): a remote event may become a local
    record ONLY if it is attributable —

    * it carries :data:`SOURCE_CATEGORY` (we projected it), or
    * its uid is in a host namespace (``study:``/``health:`` — we minted it),
      or
    * a linked row already claims it.

    Anything else is a *candidate*, never a record: a keyword match on the
    title is surfaced as a suggestion the user confirms, never written
    silently. In health especially, a false positive becomes a clinical
    record — so fuzzy matching is never an automatic write path.
    """
    if any(str(category).upper() == SOURCE_CATEGORY for category in event.categories):
        return True
    uid = event.uid or ""
    return any(uid.startswith(namespace) for namespace in namespaces)


def event_hash(event: CalendarEvent) -> str:
    return hashlib.sha256(
        json.dumps(event_content(event), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# iCalendar (RFC 5545 subset)
# ---------------------------------------------------------------------------

_ICS_LINE_LIMIT = 75
_ICS_ESCAPE = {"\\": "\\\\", ";": "\\;", ",": "\\,", "\n": "\\n", "\r": ""}
_ICS_UNESCAPE = re.compile(r"\\([\\;,nN])")


def _ics_escape(value: str) -> str:
    out = []
    for char in value.replace("\r\n", "\n").replace("\r", "\n"):
        out.append(_ICS_ESCAPE.get(char, char))
    return "".join(out)


def _ics_unescape(value: str) -> str:
    return _ICS_UNESCAPE.sub(
        lambda match: "\n" if match.group(1) in ("n", "N") else match.group(1), value
    )


def _fold(line: str) -> list[str]:
    """Fold at 75 octets, never splitting a UTF-8 sequence."""
    encoded = line.encode("utf-8")
    if len(encoded) <= _ICS_LINE_LIMIT:
        return [line]
    lines: list[str] = []
    current = b""
    for char in line:
        chunk = char.encode("utf-8")
        if len(current) + len(chunk) > _ICS_LINE_LIMIT:
            lines.append(current.decode("utf-8"))
            current = b" " + chunk
        else:
            current += chunk
    if current:
        lines.append(current.decode("utf-8"))
    return lines


def _ics_dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ics_day(value: date) -> str:
    return value.strftime("%Y%m%d")


def _event_lines(event: CalendarEvent, *, dtstamp: datetime | None = None) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{_ics_escape(event.uid)}"]
    if event.all_day and isinstance(event.start, date) and not isinstance(event.start, datetime):
        lines.append(f"DTSTART;VALUE=DATE:{_ics_day(event.start)}")
        if event.end is not None and isinstance(event.end, date):
            lines.append(f"DTEND;VALUE=DATE:{_ics_day(event.end)}")
    else:
        lines.append(f"DTSTART:{_ics_dt(_as_datetime(event.start))}")
        if event.end is not None:
            lines.append(f"DTEND:{_ics_dt(_as_datetime(event.end))}")
    if event.summary:
        lines.append(f"SUMMARY:{_ics_escape(event.summary)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_ics_escape(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_ics_escape(event.location)}")
    lines.append(f"STATUS:{event.status.upper()}")
    lines.append(f"TRANSP:{event.transparency.upper()}")
    if event.categories:
        lines.append(f"CATEGORIES:{','.join(_ics_escape(item) for item in event.categories)}")
    if event.sequence:
        lines.append(f"SEQUENCE:{event.sequence}")
    if event.url:
        lines.append(f"URL:{_ics_escape(event.url)}")
    if event.recurrence:
        lines.append(f"RRULE:{event.recurrence}")
    if event.recurrence_id:
        lines.append(f"RECURRENCE-ID:{_ics_escape(event.recurrence_id)}")
    if event.private:
        lines.append("CLASS:PRIVATE")
    for minutes in event.alarms_minutes:
        lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"TRIGGER:-PT{abs(int(minutes))}M",
                "DESCRIPTION:Reminder",
                "END:VALARM",
            ]
        )
    if event.updated_at:
        lines.append(f"LAST-MODIFIED:{_ics_dt(event.updated_at)}")
    lines.append("DTSTAMP:" + _ics_dt(dtstamp or event.updated_at or datetime.now(UTC)))
    lines.append("END:VEVENT")
    return lines


def to_ics(
    events: Iterable[CalendarEvent],
    *,
    calendar_name: str = "Neuronection",
    dtstamp: datetime | None = None,
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Neuronection//Family Cloud Calendar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
    for event in events:
        lines.extend(_event_lines(event, dtstamp=dtstamp))
    lines.append("END:VCALENDAR")
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"


@dataclass(frozen=True)
class _IcsProperty:
    name: str
    params: Mapping[str, str]
    value: str


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split_property(line: str) -> _IcsProperty | None:
    if ":" not in line:
        return None
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, raw = part.partition("=")
        params[key.upper()] = raw
    return _IcsProperty(name=name, params=params, value=value)


def _parse_ics_dt(prop: _IcsProperty, default_timezone: ZoneInfo | timezone = UTC) -> datetime | date:
    raw = prop.value.strip()
    if prop.params.get("VALUE") == "DATE" or (len(raw) == 8 and raw.isdigit()):
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    zone: ZoneInfo | timezone = default_timezone
    tzid = prop.params.get("TZID")
    if tzid:
        try:
            zone = ZoneInfo(tzid)
        except ZoneInfoNotFoundError:
            zone = default_timezone
    if raw.endswith("Z"):
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    try:
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    except ValueError as exc:
        raise CalendarError("invalid", f"unparseable DTSTART/DTEND {raw!r}") from exc
    return parsed.replace(tzinfo=zone)


def _duration_value(raw: str) -> timedelta | None:
    match = re.fullmatch(
        r"P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        raw.strip().upper(),
    )
    if not match:
        return None
    return timedelta(
        weeks=int(match.group("weeks") or 0),
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=int(match.group("seconds") or 0),
    )


def parse_ics(
    text: str,
    *,
    default_timezone: ZoneInfo | timezone = UTC,
    include_cancelled: bool = True,
) -> list[CalendarEvent]:
    """Parse VEVENTs into normalized events (recurring masters kept as rules)."""
    events: list[CalendarEvent] = []
    current: dict[str, list[_IcsProperty]] | None = None
    alarms: list[int] = []
    in_alarm = False
    for line in _unfold(text):
        prop = _split_property(line)
        if prop is None:
            continue
        if prop.name == "BEGIN" and prop.value.upper() == "VEVENT":
            current = {}
            alarms = []
            in_alarm = False
            continue
        if prop.name == "BEGIN" and prop.value.upper() == "VALARM":
            in_alarm = True
            continue
        if prop.name == "END" and prop.value.upper() == "VALARM":
            in_alarm = False
            continue
        if prop.name == "END" and prop.value.upper() == "VEVENT":
            if current is not None:
                event = _event_from_props(current, alarms, default_timezone)
                if event is not None and (include_cancelled or event.status != "cancelled"):
                    events.append(event)
            current = None
            continue
        if current is None:
            continue
        if in_alarm:
            if prop.name == "TRIGGER":
                minutes = _alarm_minutes(prop.value)
                if minutes is not None:
                    alarms.append(minutes)
            continue
        current.setdefault(prop.name, []).append(prop)
    return events


def _alarm_minutes(raw: str) -> int | None:
    match = re.fullmatch(r"-?P(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?)?", raw.strip().upper())
    if not match:
        return None
    return int(match.group("h") or 0) * 60 + int(match.group("m") or 0)


def _first(props: Mapping[str, list[_IcsProperty]], name: str) -> _IcsProperty | None:
    values = props.get(name)
    return values[0] if values else None


def _event_from_props(
    props: Mapping[str, list[_IcsProperty]],
    alarms: Sequence[int],
    default_timezone: ZoneInfo | timezone,
) -> CalendarEvent | None:
    uid_prop = _first(props, "UID")
    start_prop = _first(props, "DTSTART")
    if uid_prop is None or start_prop is None:
        return None
    start = _parse_ics_dt(start_prop, default_timezone)
    end_prop = _first(props, "DTEND")
    end: datetime | date | None = None
    if end_prop is not None:
        end = _parse_ics_dt(end_prop, default_timezone)
    else:
        duration_prop = _first(props, "DURATION")
        if duration_prop is not None:
            delta = _duration_value(duration_prop.value)
            if delta is not None:
                if isinstance(start, datetime):
                    end = start + delta
                else:
                    end = start + timedelta(days=delta.days)
    all_day = not isinstance(start, datetime)
    if all_day and end is not None and isinstance(end, datetime):
        end = end.date()

    def text_of(name: str) -> str:
        prop = _first(props, name)
        return _ics_unescape(prop.value) if prop else ""

    categories: list[str] = []
    for prop in props.get("CATEGORIES", []):
        categories.extend(
            _ics_unescape(item.strip()) for item in prop.value.split(",") if item.strip()
        )
    status_raw = text_of("STATUS").lower()
    status = status_raw if status_raw in CALENDAR_STATUSES else "confirmed"
    transp_raw = text_of("TRANSP").lower()
    transparency = "transparent" if transp_raw == "transparent" else "opaque"
    updated_prop = _first(props, "LAST-MODIFIED")
    updated = None
    if updated_prop is not None:
        parsed = _parse_ics_dt(updated_prop, default_timezone)
        if isinstance(parsed, datetime):
            updated = parsed
    sequence_raw = text_of("SEQUENCE") or "0"
    try:
        sequence = int(sequence_raw)
    except ValueError:
        sequence = 0
    private = text_of("CLASS").upper() == "PRIVATE"
    recurrence_prop = _first(props, "RRULE")
    recurrence_id_prop = _first(props, "RECURRENCE-ID")
    return CalendarEvent(
        uid=text_of("UID") or uid_prop.value,
        start=start,
        end=end,
        summary=text_of("SUMMARY"),
        all_day=all_day,
        description=text_of("DESCRIPTION"),
        location=text_of("LOCATION"),
        status=status,
        transparency=transparency,
        categories=tuple(dict.fromkeys(categories)),
        sequence=sequence,
        url=text_of("URL") or None,
        organizer=text_of("ORGANIZER") or None,
        updated_at=updated,
        recurrence=recurrence_prop.value if recurrence_prop else None,
        recurrence_id=recurrence_id_prop.value if recurrence_id_prop else None,
        alarms_minutes=tuple(sorted(set(alarms))),
        private=private,
    )


# ---------------------------------------------------------------------------
# Bounded recurrence expansion
# ---------------------------------------------------------------------------

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _rrule_parts(rule: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in rule.upper().replace("RRULE:", "").split(";"):
        key, _, value = chunk.partition("=")
        if key:
            parts[key] = value
    return parts


def expand_event(
    event: CalendarEvent,
    window_start: datetime | date,
    window_end: datetime | date,
    *,
    limit: int = MAX_OCCURRENCES,
) -> list[CalendarEvent]:
    """Expand one recurring event into occurrences intersecting the window.

    Supports FREQ DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL and
    (WEEKLY) BYDAY. Other rules return the master unchanged — hosts may use a
    provider-side expansion (Google/Graph) for the full RFC surface.
    """
    if not event.recurrence:
        return [event]
    parts = _rrule_parts(event.recurrence)
    freq = parts.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [event]
    interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until: datetime | date | None = None
    if parts.get("UNTIL"):
        raw = parts["UNTIL"]
        try:
            if len(raw) == 8 and raw.isdigit():
                until = date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
            elif raw.endswith("Z"):
                until = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            else:
                until = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            until = None
    byday = [day for day in parts.get("BYDAY", "").split(",") if day in _WEEKDAYS]

    wstart = _as_datetime(window_start)
    wend = _as_datetime(window_end)
    if wend < wstart:
        return []
    if (wend - wstart).days > MAX_RECURRENCE_DAYS:
        wend = wstart + timedelta(days=MAX_RECURRENCE_DAYS)
    wstart_day, wend_day = wstart.date(), wend.date()

    occurrences: list[CalendarEvent] = []
    base_day = event.start_day
    cursor = base_day
    index = 0
    emitted = 0
    while index < limit:
        if until is not None:
            until_day = until if isinstance(until, date) and not isinstance(until, datetime) else until.date()
            if cursor > until_day:
                break
        if count is not None and emitted >= count:
            break
        if freq == "WEEKLY" and byday:
            week_start = cursor - timedelta(days=cursor.weekday())
            candidates = [week_start + timedelta(days=_WEEKDAYS[day]) for day in byday]
        else:
            candidates = [cursor]
        for candidate in candidates:
            if candidate < base_day:
                continue
            if candidate > wend_day:
                return occurrences
            if candidate >= wstart_day:
                occurrences.append(
                    replace(
                        event,
                        start=_occurrence_start(event, candidate),
                        end=_occurrence_end(event, candidate),
                        recurrence=None,
                        recurrence_id=event.uid,
                    )
                )
                emitted += 1
                if len(occurrences) >= limit:
                    return occurrences
        index += 1
        if freq == "DAILY":
            cursor = cursor + timedelta(days=interval)
        elif freq == "WEEKLY":
            cursor = cursor + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            cursor = _add_months(cursor, interval)
        else:
            cursor = _add_months(cursor, interval * 12)
        if cursor > wend_day + timedelta(days=1):
            break
    return occurrences


def _occurrence_start(event: CalendarEvent, day: date) -> datetime | date:
    if event.all_day:
        return day
    start = _as_datetime(event.start)
    return datetime.combine(day, start.timetz().replace(tzinfo=None), tzinfo=start.tzinfo)


def _occurrence_end(event: CalendarEvent, day: date) -> datetime | date | None:
    if event.end is None:
        return None
    if event.all_day:
        return day + (event.end_day - event.start_day)
    start = _as_datetime(event.start)
    end = _as_datetime(event.end)
    return _occurrence_start(event, day) + (end - start)


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

AuthProvider = Callable[[], "str | Awaitable[str]"]


class CalendarTransport(Protocol):
    writable: bool

    async def list_events(
        self,
        start: datetime | date,
        end: datetime | date,
        *,
        page_token: str | None = None,
        sync_token: str | None = None,
    ) -> "CalendarPage": ...

    async def put_event(self, event: CalendarEvent) -> CalendarEvent: ...

    async def delete_event(self, event: CalendarEvent) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class CalendarPage:
    events: list[CalendarEvent]
    next_page_token: str | None = None
    next_sync_token: str | None = None
    not_modified: bool = False


async def _resolve_auth(auth: AuthProvider | None) -> str | None:
    if auth is None:
        return None
    value = auth()
    if hasattr(value, "__await__"):
        value = await value  # type: ignore[assignment]
    return str(value) if value else None


def basic_auth(username: str, password: str) -> str:
    import base64

    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def bearer_auth(token: str) -> str:
    return f"Bearer {token}"


class _HttpTransport:
    """Shared plumbing: injectable client, auth header, bounded retries."""

    def __init__(
        self,
        client: Any,
        auth: AuthProvider | None = None,
        *,
        timeout: float = 20.0,
    ) -> None:
        self._client = client
        self._auth = auth
        self._timeout = timeout

    async def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        auth = await _resolve_auth(self._auth)
        if auth:
            headers["Authorization"] = auth
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                url,
                headers=await self._headers(headers),
                content=content,
                params=params,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - typed at the seam
            raise CalendarError("unreachable", str(exc), retryable=True) from exc
        if response.status_code in (401, 403):
            raise CalendarError("auth", f"{method} {urlparse(url).netloc} -> {response.status_code}")
        if response.status_code == 429:
            raise CalendarError("rate-limited", "provider asked to slow down", retryable=True)
        if response.status_code >= 500:
            raise CalendarError("provider", f"{method} {urlparse(url).netloc} -> {response.status_code}", retryable=True)
        return response

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


class IcsCalendarTransport(_HttpTransport):
    """Read-only ICS feed (webcal:// and https://)."""

    writable = False

    def __init__(
        self,
        client: Any,
        url: str,
        *,
        auth: AuthProvider | None = None,
        default_timezone: ZoneInfo | timezone = UTC,
        timeout: float = 20.0,
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.url = url
        self.default_timezone = default_timezone

    async def list_events(
        self,
        start: datetime | date,
        end: datetime | date,
        *,
        page_token: str | None = None,
        sync_token: str | None = None,
    ) -> CalendarPage:
        response = await self._request("GET", self.url)
        if response.status_code == 404:
            raise CalendarError("provider", "calendar feed not found")
        if response.status_code >= 300:
            raise CalendarError("provider", f"feed -> {response.status_code}")
        events = parse_ics(response.text, default_timezone=self.default_timezone)
        expanded: list[CalendarEvent] = []
        for event in events:
            if event.recurrence:
                expanded.extend(expand_event(event, start, end))
            else:
                expanded.append(event)
        return CalendarPage(events=expanded)

    async def put_event(self, event: CalendarEvent) -> CalendarEvent:
        raise CalendarError("unsupported", "an ICS subscription is read-only")

    async def delete_event(self, event: CalendarEvent) -> None:
        raise CalendarError("unsupported", "an ICS subscription is read-only")


_DAV_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "cs": "http://calendarserver.org/ns/"}


def _dav_url(base: str, href: str) -> str:
    parsed = urlparse(href)
    if parsed.scheme:
        return href
    return urljoin(base.rstrip("/") + "/", href.lstrip("/"))


class CalDavCalendarTransport(_HttpTransport):
    """CalDAV calendar collection (Nextcloud, iCloud, Fastmail, self-hosted)."""

    def __init__(
        self,
        client: Any,
        calendar_url: str,
        *,
        auth: AuthProvider | None = None,
        default_timezone: ZoneInfo | timezone = UTC,
        timeout: float = 20.0,
        writable: bool = True,
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.calendar_url = calendar_url.rstrip("/") + "/"
        self.default_timezone = default_timezone
        self.writable = writable

    async def discover_calendars(self) -> list[dict[str, Any]]:
        """PROPFIND the principal's calendar home; returns id/name/href rows."""
        body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:displayname/><d:resourcetype/><c:supported-calendar-component-set/></d:prop>
</d:propfind>"""
        response = await self._request(
            "PROPFIND",
            self.calendar_url,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=body,
        )
        if response.status_code not in (207, 200):
            raise CalendarError("provider", f"PROPFIND -> {response.status_code}")
        root = ET.fromstring(response.text)
        rows: list[dict[str, Any]] = []
        for node in root.findall("d:response", _DAV_NS):
            href = (node.findtext("d:href", default="", namespaces=_DAV_NS) or "").strip()
            props = node.find("d:propstat/d:prop", _DAV_NS)
            if not href or props is None:
                continue
            resource = props.find("d:resourcetype", _DAV_NS)
            calendar = resource.find("c:calendar", _DAV_NS) if resource is not None else None
            if calendar is None:
                continue
            resolved = _dav_url(self.calendar_url, href)
            rows.append(
                {
                    "id": href.rstrip("/").rsplit("/", 1)[-1],
                    "name": props.findtext("d:displayname", default="", namespaces=_DAV_NS) or href,
                    "url": resolved,
                    "href": resolved,
                    "primary": False,
                }
            )
        return rows

    async def list_events(
        self,
        start: datetime | date,
        end: datetime | date,
        *,
        page_token: str | None = None,
        sync_token: str | None = None,
    ) -> CalendarPage:
        start_text = _ics_dt(_as_datetime(start)) if not isinstance(start, date) or isinstance(start, datetime) else datetime.combine(start, time.min, tzinfo=UTC).strftime("%Y%m%dT%H%M%SZ")
        end_text = _ics_dt(_as_datetime(end)) if not isinstance(end, date) or isinstance(end, datetime) else datetime.combine(end, time.max, tzinfo=UTC).strftime("%Y%m%dT%H%M%SZ")
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{start_text}" end="{end_text}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""
        response = await self._request(
            "REPORT",
            self.calendar_url,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=body,
        )
        if response.status_code == 404:
            raise CalendarError("provider", "calendar collection not found")
        if response.status_code not in (207, 200):
            raise CalendarError("provider", f"REPORT -> {response.status_code}")
        root = ET.fromstring(response.text)
        events: list[CalendarEvent] = []
        for node in root.findall("d:response", _DAV_NS):
            href = (node.findtext("d:href", default="", namespaces=_DAV_NS) or "").strip()
            props = node.find("d:propstat/d:prop", _DAV_NS)
            if props is None:
                continue
            data = props.findtext("c:calendar-data", default="", namespaces=_DAV_NS)
            etag = props.findtext("d:getetag", default="", namespaces=_DAV_NS) or None
            if not data:
                continue
            for event in parse_ics(data, default_timezone=self.default_timezone):
                events.append(replace(event, remote_id=href or event.uid, etag=etag))
        return CalendarPage(events=events)

    async def put_event(self, event: CalendarEvent) -> CalendarEvent:
        if not self.writable:
            raise CalendarError("unsupported", "calendar is read-only")
        href = event.remote_id or f"{quote(event.uid, safe='')}.ics"
        url = _dav_url(self.calendar_url, href)
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if event.etag:
            headers["If-Match"] = event.etag
        else:
            headers["If-None-Match"] = "*"
        response = await self._request("PUT", url, headers=headers, content=to_ics([event]))
        if response.status_code == 412:
            raise CalendarError("invalid", "remote event changed since it was read")
        if response.status_code >= 300:
            raise CalendarError("provider", f"PUT -> {response.status_code}")
        etag = response.headers.get("ETag") or response.headers.get("etag")
        return replace(event, remote_id=href, etag=etag or event.etag)

    async def delete_event(self, event: CalendarEvent) -> None:
        if not self.writable:
            raise CalendarError("unsupported", "calendar is read-only")
        href = event.remote_id or f"{quote(event.uid, safe='')}.ics"
        url = _dav_url(self.calendar_url, href)
        headers: dict[str, str] = {}
        if event.etag:
            headers["If-Match"] = event.etag
        response = await self._request("DELETE", url, headers=headers)
        if response.status_code not in (200, 204, 404, 410):
            raise CalendarError("provider", f"DELETE -> {response.status_code}")


_GOOGLE_NS = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarTransport(_HttpTransport):
    """Google Calendar API v3."""

    writable = True

    def __init__(
        self,
        client: Any,
        calendar_id: str = "primary",
        *,
        api_base: str = _GOOGLE_NS,
        auth: AuthProvider | None = None,
        timeout: float = 20.0,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.calendar_id = calendar_id
        self.api_base = api_base.rstrip("/")
        self.page_size = page_size

    def _url(self, suffix: str = "") -> str:
        return f"{self.api_base}/calendars/{quote(self.calendar_id, safe='')}/events{suffix}"

    async def discover_calendars(self) -> list[dict[str, Any]]:
        """`users/me/calendarList` — the picker source (ADR-0052 Rev 6)."""
        response = await self._request("GET", f"{self.api_base}/users/me/calendarList")
        if response.status_code >= 300:
            raise CalendarError("provider", f"calendarList -> {response.status_code}")
        payload = response.json()
        rows: list[dict[str, Any]] = []
        for item in payload.get("items", []) or []:
            rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("summary") or item.get("id") or ""),
                    "url": item.get("htmlLink") or "",
                    "href": item.get("htmlLink") or "",
                    "primary": bool(item.get("primary")),
                    "access": str(item.get("accessRole") or ""),
                }
            )
        return rows

    async def list_events(
        self,
        start: datetime | date,
        end: datetime | date,
        *,
        page_token: str | None = None,
        sync_token: str | None = None,
    ) -> CalendarPage:
        params: dict[str, Any] = {
            "maxResults": self.page_size,
            "singleEvents": "true",
            "showDeleted": "true",
        }
        if sync_token:
            params["syncToken"] = sync_token
        else:
            params["timeMin"] = _dt(_as_datetime(start))
            params["timeMax"] = _dt(_as_datetime(end))
            params["orderBy"] = "startTime"
        if page_token:
            params["pageToken"] = page_token
        response = await self._request("GET", self._url(), params=params)
        if response.status_code == 410:
            raise CalendarError("not-ready", "google sync token expired; full resync required")
        if response.status_code >= 300:
            raise CalendarError("provider", f"GET events -> {response.status_code}")
        payload = response.json()
        events = [
            _google_to_event(item)
            for item in payload.get("items", [])
            if item.get("status") != "cancelled" or True  # tombstones are meaningful
        ]
        return CalendarPage(
            events=events,
            next_page_token=payload.get("nextPageToken"),
            next_sync_token=payload.get("nextSyncToken"),
        )

    async def put_event(self, event: CalendarEvent) -> CalendarEvent:
        body = _event_to_google(event)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if event.remote_id:
            response = await self._request(
                "PUT",
                self._url("/" + quote(event.remote_id, safe="")),
                headers=headers,
                content=json.dumps(body),
            )
        else:
            response = await self._request("POST", self._url(), headers=headers, content=json.dumps(body))
        if response.status_code == 409:
            raise CalendarError("invalid", "google event id already exists")
        if response.status_code >= 300:
            raise CalendarError("provider", f"write event -> {response.status_code}")
        return _google_to_event(response.json())

    async def delete_event(self, event: CalendarEvent) -> None:
        if not event.remote_id:
            raise CalendarError("invalid", "delete needs a remote id")
        response = await self._request(
            "DELETE", self._url("/" + quote(event.remote_id, safe=""))
        )
        if response.status_code not in (200, 204, 404, 410):
            raise CalendarError("provider", f"DELETE -> {response.status_code}")


def _all_day_end(event: "CalendarEvent") -> date:
    """The exclusive all-day end, never before start + 1 day.

    The canonical model allows the single-day shorthand ``end == start``;
    providers require ``end > start`` and treat ``end`` as exclusive. Clamp so
    both spellings write one day — never zero, never two (E2).
    """
    end = event.end_day
    start = event.start_day
    return end if end > start else start + timedelta(days=1)


def _google_time(value: Mapping[str, Any]) -> datetime | date | None:
    if not value:
        return None
    if value.get("date"):
        return _parse_day(str(value["date"]))
    if value.get("dateTime"):
        return _parse_dt(str(value["dateTime"]))
    return None


def _google_to_event(item: Mapping[str, Any]) -> CalendarEvent:
    start = _google_time(item.get("start") or {})
    if start is None:
        raise CalendarError("provider", "google event without start")
    all_day = isinstance(start, date) and not isinstance(start, datetime)
    organizer = (item.get("organizer") or {}).get("email")
    updated_raw = item.get("updated")
    categories = [str(value) for value in item.get("categories") or ()]
    if not categories:
        # E8: Google has no categories field — ours ride extendedProperties.
        marker = (
            (item.get("extendedProperties") or {}).get("private") or {}
        ).get("nxCategories")
        if marker:
            categories = [part for part in str(marker).split(",") if part]
    private = item.get("visibility") == "private"
    # E1: iCalUID IS the identity (it is what WE push as the uid). Google's
    # `id` is an opaque handle and changes across calendars/accounts — keying
    # on it re-pulls every pushed event as new and duplicates the local row.
    ical_uid = item.get("iCalUID") or item.get("iCalUId")
    return CalendarEvent(
        uid=str(ical_uid or item.get("id")),
        start=start,
        end=_google_time(item.get("end") or {}),
        all_day=all_day,
        summary=str(item.get("summary") or ""),
        description=str(item.get("description") or ""),
        location=str(item.get("location") or ""),
        status=str(item.get("status") or "confirmed"),
        transparency="transparent" if item.get("transparency") == "transparent" else "opaque",
        categories=tuple(categories),
        sequence=int(item.get("sequence") or 0),
        url=item.get("htmlLink"),
        organizer=organizer,
        remote_id=str(item.get("id")),
        etag=item.get("etag"),
        updated_at=_parse_dt(str(updated_raw)) if updated_raw else None,
        recurrence=(item.get("recurrence") or [None])[0],
        recurrence_id=item.get("recurringEventId"),
        private=private,
        # Google never exposes attendee emails through this contract.
        alarms_minutes=tuple(
            abs(int(str(reminder.get("minutes", 0))))
            for reminder in ((item.get("reminders") or {}).get("overrides") or [])
            if reminder.get("method") == "popup"
        ),
    )


def _event_to_google(event: CalendarEvent, *, allow_recurrence: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": event.summary,
        "description": event.description,
        "location": event.location,
        "status": event.status,
        "transparency": event.transparency,
        "sequence": event.sequence,
    }
    # E1: our uid travels as iCalUID so the round trip preserves identity.
    if event.uid:
        body["iCalUID"] = event.uid
    if event.all_day:
        # E2: our all-day end is ALREADY exclusive (RFC 5545) and so is
        # Google's `end.date`. Adding a day here made a one-day event two
        # days — and it grew another day on every sync cycle.
        body["start"] = {"date": _day(event.start_day)}
        body["end"] = {"date": _day(_all_day_end(event))}
    else:
        body["start"] = {"dateTime": _dt(_as_datetime(event.start))}
        body["end"] = {
            "dateTime": _dt(_as_datetime(event.end)) if event.end else _dt(_as_datetime(event.start) + timedelta(hours=1))
        }
    if event.recurrence:
        if not allow_recurrence:
            # H1: recurrence is read-only in v1 — a series write is refused
            # uniformly (Graph already refused; Google must too).
            raise CalendarError(
                "unsupported", "recurring events are read-only in v1 — edit the series in the provider"
            )
        body["recurrence"] = [f"RRULE:{event.recurrence}"]
    if event.private:
        body["visibility"] = "private"
    # E8: Google Calendar has NO categories field. Our category vocabulary
    # (accept filters, the NEURONECTION marker) rides private extended
    # properties — invisible to the user, round-tripped by the API.
    if event.categories:
        body.setdefault("extendedProperties", {}).setdefault("private", {})[
            "nxCategories"
        ] = ",".join(event.categories)
    body.setdefault("extendedProperties", {}).setdefault("private", {})["nxSource"] = "neuronection"
    if event.alarms_minutes:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": abs(int(minutes))}
                for minutes in event.alarms_minutes
            ],
        }
    return body


class GraphCalendarTransport(_HttpTransport):
    """Microsoft Graph calendar (``/me`` by default, ``/users/{id}`` optional)."""

    writable = True

    def __init__(
        self,
        client: Any,
        calendar_id: str = "",
        *,
        api_base: str = "https://graph.microsoft.com/v1.0",
        user: str = "me",
        auth: AuthProvider | None = None,
        timeout: float = 20.0,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.calendar_id = calendar_id
        self.api_base = api_base.rstrip("/")
        self.user = user
        self.page_size = page_size

    def _base(self) -> str:
        # E6: Graph has no `/users/me` — `me` is its own segment. Emitting
        # /users/me/... 404s on every call.
        root = "/me" if self.user == "me" else f"/users/{quote(self.user, safe='')}"
        if self.calendar_id:
            return f"{self.api_base}{root}/calendars/{quote(self.calendar_id, safe='')}"
        return f"{self.api_base}{root}"

    async def discover_calendars(self) -> list[dict[str, Any]]:
        """/me/calendars — the picker source (ADR-0052 Rev 6)."""
        response = await self._request("GET", f"{self._base().rsplit('/calendars/', 1)[0]}/me/calendars")
        if response.status_code >= 300:
            raise CalendarError("provider", f"me/calendars -> {response.status_code}")
        payload = response.json()
        rows: list[dict[str, Any]] = []
        for item in payload.get("value", []) or []:
            rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or item.get("id") or ""),
                    "url": item.get("webLink") or "",
                    "href": item.get("webLink") or "",
                    "primary": bool(item.get("isDefaultCalendar")),
                    "access": str(item.get("owner", {}).get("name") or ""),
                }
            )
        return rows

    async def list_events(
        self,
        start: datetime | date,
        end: datetime | date,
        *,
        page_token: str | None = None,
        sync_token: str | None = None,
    ) -> CalendarPage:
        headers = {"Prefer": 'outlook.timezone="UTC"'}
        if page_token:
            url, params = page_token, None
        else:
            params = {
                "$top": self.page_size,
                "$select": "id,iCalUId,subject,bodyPreview,body,location,start,end,isAllDay,showAs,sensitivity,categories,lastModifiedDateTime,webLink,changeKey,isCancelled,seriesMasterId",
            }
            if sync_token:
                url = f"{self._base()}/events/delta"
                params["$deltatoken"] = sync_token
            else:
                url = f"{self._base()}/calendarView"
                params["startDateTime"] = _dt(_as_datetime(start))
                params["endDateTime"] = _dt(_as_datetime(end))
        response = await self._request("GET", url, headers=headers, params=params)
        if response.status_code in (404, 410):
            raise CalendarError("not-ready", "graph delta token invalid; full resync required")
        if response.status_code >= 300:
            raise CalendarError("provider", f"GET calendarView -> {response.status_code}")
        payload = response.json()
        return CalendarPage(
            events=[_graph_to_event(item) for item in payload.get("value", [])],
            next_page_token=payload.get("@odata.nextLink"),
            next_sync_token=payload.get("@odata.deltaLink"),
        )

    async def put_event(self, event: CalendarEvent) -> CalendarEvent:
        body = _event_to_graph(event)
        if event.remote_id:
            response = await self._request(
                "PATCH",
                f"{self._base()}/events/{quote(event.remote_id, safe='')}",
                headers={"Content-Type": "application/json", "Prefer": 'outlook.timezone="UTC"'},
                content=json.dumps(body),
            )
        else:
            response = await self._request(
                "POST",
                f"{self._base()}/events",
                headers={"Content-Type": "application/json", "Prefer": 'outlook.timezone="UTC"'},
                content=json.dumps(body),
            )
        if response.status_code == 409:
            raise CalendarError("invalid", "graph event already exists")
        if response.status_code >= 300:
            raise CalendarError("provider", f"write event -> {response.status_code}")
        return _graph_to_event(response.json())

    async def delete_event(self, event: CalendarEvent) -> None:
        if not event.remote_id:
            raise CalendarError("invalid", "delete needs a remote id")
        response = await self._request(
            "DELETE", f"{self._base()}/events/{quote(event.remote_id, safe='')}"
        )
        if response.status_code not in (200, 204, 404, 410):
            raise CalendarError("provider", f"DELETE -> {response.status_code}")


def _graph_dt(value: Mapping[str, Any] | None) -> datetime | date | None:
    if not value:
        return None
    raw = value.get("dateTime")
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    zone = value.get("timeZone")
    if parsed.tzinfo is None:
        if zone and zone.upper() != "UTC":
            try:
                parsed = parsed.replace(tzinfo=ZoneInfo(zone))
            except ZoneInfoNotFoundError:
                parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _graph_to_event(item: Mapping[str, Any]) -> CalendarEvent:
    start = _graph_dt(item.get("start"))
    if start is None:
        raise CalendarError("provider", "graph event without start")
    all_day = bool(item.get("isAllDay"))
    if all_day and isinstance(start, datetime):
        start = start.date()
    end = _graph_dt(item.get("end"))
    if all_day and isinstance(end, datetime):
        end = end.date()
    show_as = str(item.get("showAs") or "busy")
    cancelled = bool(item.get("isCancelled"))
    updated_raw = item.get("lastModifiedDateTime")
    return CalendarEvent(
        uid=str(item.get("iCalUId") or item.get("id")),
        start=start,
        end=end,
        all_day=all_day,
        summary=str(item.get("subject") or ""),
        description=str(item.get("bodyPreview") or ""),
        location=str((item.get("location") or {}).get("displayName") or ""),
        status="cancelled" if cancelled else "confirmed",
        transparency="transparent" if show_as in ("free", "workingElsewhere") else "opaque",
        categories=tuple(str(value) for value in item.get("categories") or ()),
        url=item.get("webLink"),
        remote_id=str(item.get("id")),
        etag=item.get("changeKey"),
        updated_at=_parse_dt(updated_raw) if updated_raw else None,
        recurrence_id=item.get("seriesMasterId"),
        private=str(item.get("sensitivity") or "").lower() == "private",
    )


def _event_to_graph(event: CalendarEvent) -> dict[str, Any]:
    def stamp(value: datetime | date) -> dict[str, str]:
        if isinstance(value, date) and not isinstance(value, datetime):
            return {"dateTime": f"{value.isoformat()}T00:00:00", "timeZone": "UTC"}
        return {"dateTime": _as_datetime(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}

    start_v = event.start
    end_v = _all_day_end(event) if event.all_day else (event.end or event.start)
    body: dict[str, Any] = {
        "subject": event.summary,
        "body": {"contentType": "text", "content": event.description},
        "location": {"displayName": event.location},
        "start": stamp(start_v),
        "end": stamp(end_v),
        "isAllDay": event.all_day,
        "showAs": "free" if event.transparency == "transparent" else "busy",
        "sensitivity": "private" if event.private else "normal",
    }
    if event.categories:
        body["categories"] = list(event.categories)
    if event.recurrence:
        # Graph's recurrence shape is not a trivial RRULE mapping; refuse
        # rather than silently writing a single occurrence of a series.
        raise CalendarError(
            "unsupported", "recurring-event writes are not supported over Microsoft Graph v1"
        )
    return body


# ---------------------------------------------------------------------------
# Reconciliation — pure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalEvent:
    """A host record projected into calendar space.

    ``content_hash`` is :func:`event_hash` of ``event`` at the last local
    write; ``deleted`` is a tombstone the host keeps until the sync applies
    the deletion. ``remote_id``/``etag`` are the last observed remote state.
    """

    local_id: str
    event: CalendarEvent
    updated_at: datetime
    content_hash: str | None = None
    deleted: bool = False

    def __post_init__(self) -> None:
        if self.content_hash is None:
            object.__setattr__(self, "content_hash", event_hash(self.event))


@dataclass(frozen=True)
class EventLink:
    """The sync identity + change pins for one local↔remote pair.

    ``local_hash`` / ``remote_hash`` are :func:`event_hash` of the LOCAL and
    REMOTE content as last reconciled — **separately**. A single shared hash
    cannot work: providers normalize on write, so hashing one side against
    the other reports a phantom change on every run (E3).
    """

    local_id: str
    uid: str
    remote_id: str | None
    etag: str | None
    local_hash: str | None = None
    remote_hash: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SyncAction:
    kind: ActionKind
    uid: str
    local_id: str | None = None
    event: CalendarEvent | None = None
    remote_id: str | None = None
    reason: str = ""

    @property
    def mutates_remote(self) -> bool:
        return self.kind in (
            ActionKind.CREATE_REMOTE,
            ActionKind.UPDATE_REMOTE,
            ActionKind.DELETE_REMOTE,
        )


@dataclass(frozen=True)
class SyncPlan:
    actions: list[SyncAction]
    direction: SyncDirection
    remote_writable: bool
    skipped: int = 0

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.kind.value] = counts.get(action.kind.value, 0) + 1
        return counts


def reconcile(
    local: Iterable[LocalEvent],
    remote: Iterable[CalendarEvent],
    links: Iterable[EventLink],
    *,
    direction: SyncDirection | str = SyncDirection.TWO_WAY,
    conflicts: ConflictPolicy | str = ConflictPolicy.SKIP,
    deletions: DeletionPolicy | str = DeletionPolicy.KEEP_LOCAL,
    accept: Callable[[CalendarEvent], bool] | None = None,
    remote_writable: bool = True,
    now: datetime | None = None,
    window: tuple[datetime | date, datetime | date] | None = None,
    skew: timedelta = timedelta(minutes=5),
) -> SyncPlan:
    """Compare local records, remote events and links; return host actions.

    The function never performs I/O and never mutates its inputs, so the
    same plan can be tested, logged (without credentials) and retried.
    """
    direction = SyncDirection(direction)
    conflicts = ConflictPolicy(conflicts)
    deletions = DeletionPolicy(deletions)
    now = (now or datetime.now(UTC)).astimezone(UTC)

    local_by_uid = {record.event.uid: record for record in local}
    accepted = [event for event in remote if accept is None or accept(event)]
    # E9/identity law: the LINK ROW is the identity authority. A provider may
    # mint its own uid (Graph's `iCalUId` is server-generated and read-only),
    # so the remote side is keyed by its WRITE HANDLE (`remote_id`), falling
    # back to uid. Matching by uid alone silently split one event into two.
    remote_by_key = {event.remote_id or event.uid: event for event in accepted}
    remote_by_uid = {event.uid: event for event in accepted}
    link_by_uid = {link.uid: link for link in links}
    actions: list[SyncAction] = []
    skipped = 0

    for uid, link in link_by_uid.items():
        local_record = local_by_uid.get(uid)
        remote_event = remote_by_key.get(link.remote_id or "") or remote_by_uid.get(uid)
        if local_record is None:
            # The host dropped the local record entirely: treat as unlink.
            actions.append(SyncAction(ActionKind.UNLINK, uid, local_id=link.local_id, reason="local-missing"))
            continue
        if local_record.deleted:
            if (
                deletions == DeletionPolicy.PROPAGATE
                and direction != SyncDirection.PULL
                and remote_event is not None
                and remote_writable
            ):
                actions.append(
                    SyncAction(ActionKind.DELETE_REMOTE, uid, local_id=link.local_id,
                               event=remote_event, remote_id=remote_event.remote_id, reason="local-deleted")
                )
            elif deletions == DeletionPolicy.KEEP_REMOTE:
                # Keep the remote block; only the local side forgets it.
                actions.append(SyncAction(ActionKind.UNLINK, uid, local_id=link.local_id, reason="local-deleted-keep-remote"))
            elif deletions == DeletionPolicy.IGNORE:
                skipped += 1
            else:
                actions.append(SyncAction(ActionKind.UNLINK, uid, local_id=link.local_id, reason="local-deleted"))
            continue
        if remote_event is None:
            if window is not None and not _in_window(local_record.event, window):
                # The remote list is window-bounded: an event outside the
                # window is ABSENT, not deleted. Unlinking it here would
                # resurrect (and duplicate) it on the next run (E5).
                skipped += 1
                continue
            if direction == SyncDirection.PULL:
                skipped += 1
                continue
            if deletions == DeletionPolicy.KEEP_REMOTE and remote_writable:
                actions.append(
                    SyncAction(ActionKind.CREATE_REMOTE, uid, local_id=local_record.local_id,
                               event=_with_uid(local_record.event, uid), reason="remote-vanished")
                )
            else:
                actions.append(SyncAction(ActionKind.UNLINK, uid, local_id=local_record.local_id, reason="remote-vanished"))
            continue

        # E3: compare each side against ITS OWN pin. A missing pin reads as
        # "changed" only for the side that has no pin yet (first reconcile).
        local_changed = link.local_hash is None or event_hash(local_record.event) != link.local_hash
        remote_changed = link.remote_hash is None or event_hash(remote_event) != link.remote_hash
        if local_changed and remote_changed:
            if link.local_hash is None and link.remote_hash is None:
                # Neither side is pinned yet: adopt the remote as truth once,
                # never treat an unpinned pair as a conflict (E3 tail).
                actions.append(
                    SyncAction(ActionKind.UPDATE_LOCAL, uid, local_id=local_record.local_id,
                               event=remote_event, reason="pin-remote")
                )
                continue
            winner = _resolve_conflict(conflicts, local_record, remote_event, link, now, skew)
            if winner is None:
                skipped += 1
                actions.append(
                    SyncAction(ActionKind.CONFLICT, uid, local_id=link.local_id, event=remote_event,
                               reason="both-changed")
                )
            elif winner == "local":
                actions.append(
                    SyncAction(ActionKind.UPDATE_REMOTE, uid, local_id=local_record.local_id,
                               event=_with_remote_identity(_with_uid(local_record.event, uid), link),
                               remote_id=link.remote_id, reason="conflict-local")
                )
            else:
                actions.append(
                    SyncAction(ActionKind.UPDATE_LOCAL, uid, local_id=local_record.local_id,
                               event=remote_event, reason="conflict-remote")
                )
        elif local_changed:
            if direction == SyncDirection.PULL or not remote_writable:
                skipped += 1
            else:
                actions.append(
                    SyncAction(ActionKind.UPDATE_REMOTE, uid, local_id=local_record.local_id,
                               event=_with_remote_identity(_with_uid(local_record.event, uid), link),
                               remote_id=link.remote_id, reason="local-changed")
                )
        elif remote_changed:
            if direction == SyncDirection.PUSH:
                skipped += 1
            else:
                actions.append(
                    SyncAction(ActionKind.UPDATE_LOCAL, uid, local_id=local_record.local_id,
                               event=remote_event, reason="remote-changed")
                )
        else:
            skipped += 1

    # Remotes already claimed by a link (or by an orphan repair below) must
    # never be re-created as a duplicate — shared across both write passes.
    claimed = {(link.remote_id or link.uid) for link in link_by_uid.values()}
    claimed |= set(link_by_uid)

    if direction != SyncDirection.PULL:
        if not remote_writable:
            skipped += sum(1 for record in local_by_uid.values() if record.event.uid not in link_by_uid)
        else:
            # E9 crash-window repair: a local record with no link may already
            # have a remote twin whose link write was lost (crash between the
            # provider write and the local commit). Match by CONTENT HASH and
            # attach — creating again would duplicate the user's event.
            orphans = {
                event_hash(event): event
                for event in accepted
                if (event.remote_id or event.uid) not in claimed
            }
            for uid, record in local_by_uid.items():
                if uid in link_by_uid:
                    continue
                if record.deleted:
                    skipped += 1
                    continue
                twin = orphans.pop(event_hash(record.event), None)
                if twin is not None:
                    claimed.add(twin.remote_id or twin.uid)
                    actions.append(
                        SyncAction(ActionKind.LINK_EXISTING, uid, local_id=record.local_id,
                                   event=twin, remote_id=twin.remote_id,
                                   reason="orphan-repair")
                    )
                    continue
                actions.append(
                    SyncAction(ActionKind.CREATE_REMOTE, uid, local_id=record.local_id,
                               event=_with_uid(record.event, uid), reason="local-new")
                )

    if direction != SyncDirection.PUSH:
        # A remote is "claimed" when a link holds its write handle OR an
        # orphan-repair just attached it — its uid may be provider-minted, so
        # uid alone is not the test (E9).
        for uid, event in remote_by_uid.items():
            if uid in claimed or (event.remote_id or "") in claimed:
                continue
            if event.status == "cancelled":
                skipped += 1
                continue
            actions.append(
                SyncAction(ActionKind.CREATE_LOCAL, uid, event=event, remote_id=event.remote_id,
                           reason="remote-new")
            )
    else:
        skipped += sum(1 for uid in remote_by_uid if uid not in link_by_uid)

    return SyncPlan(actions=actions, direction=direction, remote_writable=remote_writable, skipped=skipped)


def _with_uid(event: CalendarEvent, uid: str) -> CalendarEvent:
    return event if event.uid == uid else replace(event, uid=uid)


def _with_remote_identity(event: CalendarEvent, link: EventLink) -> CalendarEvent:
    """Carry the last known remote id/etag into a write (PATCH/PUT target)."""
    return replace(
        event,
        remote_id=event.remote_id or link.remote_id,
        etag=event.etag or link.etag,
    )


def _resolve_conflict(
    policy: ConflictPolicy,
    local: LocalEvent,
    remote: CalendarEvent,
    link: EventLink,
    now: datetime,
    skew: timedelta = timedelta(minutes=5),
) -> str | None:
    if policy == ConflictPolicy.LOCAL_WINS:
        return "local"
    if policy == ConflictPolicy.REMOTE_WINS:
        return "remote"
    if policy == ConflictPolicy.SKIP:
        return None
    # Clocks here are DIFFERENT clocks (host vs provider). `newest-wins` is a
    # tiebreaker only: within the skew tolerance there is no safe winner, so
    # the conflict is surfaced rather than silently resolved (C2).
    local_at = _ensure_utc(local.updated_at or now)
    remote_at = _ensure_utc(remote.updated_at or link.updated_at or now)
    if abs((local_at - remote_at).total_seconds()) <= skew.total_seconds():
        return None
    return "local" if local_at > remote_at else "remote"


def _in_window(event: CalendarEvent, window: tuple[datetime | date, datetime | date]) -> bool:
    """True when the event's span intersects the window (all-day aware)."""
    w_start = window[0].date() if isinstance(window[0], datetime) else window[0]
    w_end = window[1].date() if isinstance(window[1], datetime) else window[1]
    start = event.start_day
    end = event.end_day
    return start <= w_end and end >= w_start


def _ensure_utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def apply_action_to_link(
    action: SyncAction,
    *,
    local_id: str | None,
    event: CalendarEvent | None,
    now: datetime | None = None,
) -> EventLink | None:
    """Build the link row a host stores after executing a sync action."""
    at = now or datetime.now(UTC)
    if action.kind == ActionKind.UNLINK or action.kind == ActionKind.DELETE_REMOTE:
        return None
    if action.kind == ActionKind.LINK_EXISTING:
        # Attach an orphan remote to its local record — no write happened.
        return EventLink(
            local_id=local_id or action.local_id or "",
            uid=local_id or action.uid,
            remote_id=event.remote_id if event else action.remote_id,
            etag=event.etag if event else None,
            local_hash=event_hash(event) if event else None,
            remote_hash=event_hash(event) if event else None,
            updated_at=at,
        )
    if event is None:
        return None
    return EventLink(
        local_id=local_id or action.local_id or "",
        uid=event.uid,
        remote_id=event.remote_id,
        etag=event.etag,
        local_hash=event_hash(event),
        remote_hash=event_hash(event),
        updated_at=at,
    )


def transport_for(
    protocol: str,
    client: Any,
    *,
    auth: AuthProvider | None = None,
    calendar_id: str = "",
    calendar_url: str | None = None,
    url: str | None = None,
    api_base: str | None = None,
    default_timezone: ZoneInfo | timezone = UTC,
    writable: bool = True,
) -> CalendarTransport:
    """Build a calendar transport from a catalog ``protocol`` value."""
    if protocol == "ics":
        if not url:
            raise CalendarError("invalid", "ics needs a feed URL")
        return IcsCalendarTransport(client, url, auth=auth, default_timezone=default_timezone)
    if protocol == "caldav":
        if not calendar_url:
            raise CalendarError("invalid", "caldav needs a calendar URL")
        return CalDavCalendarTransport(
            client,
            calendar_url,
            auth=auth,
            default_timezone=default_timezone,
            writable=writable,
        )
    if protocol == "google-calendar":
        return GoogleCalendarTransport(
            client,
            calendar_id or "primary",
            api_base=api_base or _GOOGLE_NS,
            auth=auth,
        )
    if protocol == "graph-calendar":
        return GraphCalendarTransport(
            client,
            calendar_id,
            api_base=api_base or "https://graph.microsoft.com/v1.0",
            auth=auth,
        )
    raise CalendarError("unsupported", f"unknown calendar protocol {protocol!r}")


__all__ = [
    "ActionKind",
    "CalendarError",
    "CalendarEvent",
    "CalendarPage",
    "CalendarTransport",
    "CalDavCalendarTransport",
    "ConflictPolicy",
    "DEFAULT_PAGE_SIZE",
    "DeletionPolicy",
    "EventLink",
    "GoogleCalendarTransport",
    "GraphCalendarTransport",
    "IcsCalendarTransport",
    "LocalEvent",
    "MAX_OCCURRENCES",
    "REDACTED_SUMMARY",
    "SyncAction",
    "SyncDirection",
    "SOURCE_CATEGORY",
    "SyncPlan",
    "is_attributable",
    "apply_action_to_link",
    "basic_auth",
    "bearer_auth",
    "event_content",
    "event_from_dict",
    "event_hash",
    "event_to_dict",
    "expand_event",
    "parse_ics",
    "reconcile",
    "to_ics",
    "transport_for",
]

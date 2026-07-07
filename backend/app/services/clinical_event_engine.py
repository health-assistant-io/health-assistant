"""Clinical Event engine — type-driven journey intelligence.

A pure-Python rules layer that derives computed insights from a
:class:`ClinicalEvent` and its :class:`ClinicalEventType` template. Given a
journey + its type, the engine answers:

- *What's the current phase?* (from ``type.phases`` + ``event.onset_date``)
- *Which milestones are upcoming / overdue?* (from ``type.milestones``)
- *Which biomarkers should I suggest tracking?* (from
  :class:`BiomarkerEventCorrelation`)
- *Is this journey overdue?* (``type.default_duration_days`` elapsed while still
  ACTIVE)

This is the modular, extensible core of "behavior-driving types": a new journey
type = a new JSONB template = new behavior, with **no engine code change**. The
engine has no DB dependency of its own — callers pass already-loaded
event/type/correlation data, so it's trivially testable and reusable from REST,
the AI tools, and analytics.
"""

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class JourneyInsights:
    """Computed insights for a single clinical event."""

    current_phase: Optional[Dict[str, Any]] = None
    upcoming_milestones: List[Dict[str, Any]] = field(default_factory=list)
    overdue_milestones: List[Dict[str, Any]] = field(default_factory=list)
    recommended_biomarkers: List[Dict[str, Any]] = field(default_factory=list)
    is_overdue: bool = False
    days_since_onset: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_phase": self.current_phase,
            "upcoming_milestones": self.upcoming_milestones,
            "overdue_milestones": self.overdue_milestones,
            "recommended_biomarkers": self.recommended_biomarkers,
            "is_overdue": self.is_overdue,
            "days_since_onset": self.days_since_onset,
        }


def compute_insights(
    event: Any,
    *,
    type_template: Optional[Any] = None,
    recommended_biomarkers: Optional[List[Dict[str, Any]]] = None,
    now: Optional[_dt.datetime] = None,
) -> JourneyInsights:
    """Compute journey insights for ``event``.

    Args:
        event: a ``ClinicalEvent`` (or any object with ``onset_date``,
            ``resolved_date``, ``status``, ``event_metadata``).
        type_template: the ``ClinicalEventType`` (or dict) carrying ``phases``,
            ``milestones``, ``default_duration_days``. Falls back to
            ``event.type_entity`` if omitted.
        recommended_biomarkers: pre-resolved biomarker dicts (from
            ``BiomarkerEventCorrelation``). If None, the field is empty.
        now: override "now" (for deterministic tests).

    The engine never raises on partial data — missing templates / dates simply
    yield empty insights, so callers can always ask.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    tpl = type_template if type_template is not None else getattr(event, "type_entity", None)

    phases = _as_list(_get(tpl, "phases"))
    milestones = _as_list(_get(tpl, "milestones"))
    default_duration = _get(tpl, "default_duration_days")

    onset = _ensure_aware(getattr(event, "onset_date", None))
    resolved = _ensure_aware(getattr(event, "resolved_date", None))
    status_value = _status_value(getattr(event, "status", None))
    metadata = getattr(event, "event_metadata", None) or {}

    insights = JourneyInsights(
        recommended_biomarkers=list(recommended_biomarkers or [])
    )

    # Days since onset + overdue flag.
    if onset is not None:
        insights.days_since_onset = max(0, (now - onset).days)
        if (
            default_duration
            and status_value == "active"
            and resolved is None
            and insights.days_since_onset > int(default_duration)
        ):
            insights.is_overdue = True

    # Current phase: the phase whose [start, end) offset window (relative to
    # onset) contains ``now``. Phases without offsets are skipped.
    if onset is not None and phases:
        elapsed_days = (now - onset).days
        for phase in phases:
            start_off = _coerce_int(_get(phase, "start_offset_days"))
            end_off = _coerce_int(_get(phase, "end_offset_days"))
            if start_off is None or end_off is None:
                continue
            if start_off <= elapsed_days < end_off:
                insights.current_phase = dict(phase)
                break

    # Milestones: each may carry an absolute ``date`` (ISO string) or a
    # ``date_field`` naming a key in event_metadata whose value is an ISO date.
    # ``alert_before_days`` surfaces a milestone as "upcoming" when within that
    # window before its date.
    for ms in milestones:
        ms_date = _milestone_date(ms, metadata)
        if ms_date is None:
            continue
        alert_before = _coerce_int(_get(ms, "alert_before_days")) or 0
        entry = {
            "name": _get(ms, "name"),
            "date": ms_date.isoformat(),
            "alert_before_days": alert_before,
        }
        if ms_date < now:
            insights.overdue_milestones.append(entry)
        elif (ms_date - now).days <= max(alert_before, 0):
            insights.upcoming_milestones.append(entry)

    return insights


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` off an ORM object or dict uniformly."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(value: Any) -> List[Any]:
    if not value:
        return []
    return list(value)


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_value(status: Any) -> str:
    if status is None:
        return ""
    # Enum (has .value) or plain string.
    val = getattr(status, "value", status)
    return str(val).lower()


def _ensure_aware(dt_value: Any) -> Optional[_dt.datetime]:
    if dt_value is None:
        return None
    if isinstance(dt_value, str):
        try:
            dt_value = _dt.datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt_value, _dt.datetime):
        return None
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=_dt.timezone.utc)
    return dt_value


def _milestone_date(ms: Any, metadata: Dict[str, Any]) -> Optional[_dt.datetime]:
    """Resolve a milestone's date from either an absolute ``date`` or a
    ``date_field`` key into ``event_metadata``."""
    raw = _get(ms, "date")
    if raw is None:
        field = _get(ms, "date_field")
        if field:
            raw = metadata.get(field) if isinstance(metadata, dict) else None
    return _ensure_aware(raw)

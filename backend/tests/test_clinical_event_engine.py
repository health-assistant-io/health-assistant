"""Unit tests for the ClinicalEventEngine — pure-Python rules layer, no DB.

These verify the "behavior-driving types" computations: current phase from
onset + phase offsets, upcoming/overdue milestones (absolute or via
``date_field`` into event_metadata), the overdue flag, and recommended
biomarker pass-through.
"""
import datetime as _dt

import pytest

from app.models.clinical_event import ClinicalEvent, ClinicalEventType
from app.models.enums import ClinicalEventStatus
from app.services.clinical_event_engine import compute_insights


NOW = _dt.datetime(2026, 4, 15, tzinfo=_dt.timezone.utc)


def _event(**kw):
    defaults = dict(
        patient_id=None,
        title="J",
        status=ClinicalEventStatus.ACTIVE,
    )
    defaults.update(kw)
    return ClinicalEvent(**defaults)


def test_current_phase_computed_from_onset_and_offsets():
    type_tpl = ClinicalEventType(
        name="Pregnancy",
        slug="pregnancy",
        phases=[
            {"name": "T1", "start_offset_days": 0, "end_offset_days": 90},
            {"name": "T2", "start_offset_days": 90, "end_offset_days": 180},
            {"name": "T3", "start_offset_days": 180, "end_offset_days": 280},
        ],
    )
    # Onset 100 days ago → T2.
    onset = NOW - _dt.timedelta(days=100)
    event = _event(onset_date=onset)
    out = compute_insights(event, type_template=type_tpl, now=NOW)
    assert out.current_phase["name"] == "T2"
    assert out.days_since_onset == 100


def test_no_phases_yields_no_current_phase():
    event = _event(onset_date=NOW - _dt.timedelta(days=10))
    out = compute_insights(event, type_template=ClinicalEventType(name="X", slug="x"), now=NOW)
    assert out.current_phase is None


def test_overdue_flag_when_duration_elapsed_and_active():
    type_tpl = ClinicalEventType(
        name="Acute", slug="acute", default_duration_days=30
    )
    event = _event(
        onset_date=NOW - _dt.timedelta(days=45),
        status=ClinicalEventStatus.ACTIVE,
    )
    out = compute_insights(event, type_template=type_tpl, now=NOW)
    assert out.is_overdue is True
    assert out.days_since_onset == 45


def test_overdue_flag_not_set_when_resolved():
    type_tpl = ClinicalEventType(
        name="Acute", slug="acute", default_duration_days=30
    )
    event = _event(
        onset_date=NOW - _dt.timedelta(days=45),
        resolved_date=NOW - _dt.timedelta(days=1),
        status=ClinicalEventStatus.RESOLVED,
    )
    out = compute_insights(event, type_template=type_tpl, now=NOW)
    assert out.is_overdue is False


def test_milestone_absolute_date_upcoming_within_alert_window():
    type_tpl = ClinicalEventType(
        name="Preg",
        slug="preg",
        milestones=[
            {
                "name": "EDD",
                "date": (NOW + _dt.timedelta(days=10)).date().isoformat(),
                "alert_before_days": 14,
            }
        ],
    )
    event = _event(onset_date=NOW - _dt.timedelta(days=180))
    out = compute_insights(event, type_template=type_tpl, now=NOW)
    assert len(out.upcoming_milestones) == 1
    assert out.upcoming_milestones[0]["name"] == "EDD"
    assert out.overdue_milestones == []


def test_milestone_date_field_resolved_from_event_metadata():
    type_tpl = ClinicalEventType(
        name="Preg",
        slug="preg",
        milestones=[
            {"name": "EDD", "date_field": "edd", "alert_before_days": 30},
        ],
    )
    edd = (NOW + _dt.timedelta(days=20)).date().isoformat()
    event = _event(
        onset_date=NOW - _dt.timedelta(days=180),
        event_metadata={"edd": edd},
    )
    out = compute_insights(event, type_template=type_tpl, now=NOW)
    assert len(out.upcoming_milestones) == 1
    assert out.upcoming_milestones[0]["date"] == _dt.datetime.fromisoformat(edd).replace(
        tzinfo=_dt.timezone.utc
    ).isoformat()


def test_milestone_past_date_is_overdue():
    type_tpl = ClinicalEventType(
        name="Preg",
        slug="preg",
        milestones=[{"name": "Scan", "date": (NOW - _dt.timedelta(days=5)).date().isoformat()}],
    )
    event = _event(onset_date=NOW - _dt.timedelta(days=100))
    out = compute_insights(event, type_template=type_tpl, now=NOW)
    assert len(out.overdue_milestones) == 1
    assert out.upcoming_milestones == []


def test_recommended_biomarkers_passed_through():
    bios = [{"slug": "hba1c", "name": "HbA1c"}]
    event = _event()
    out = compute_insights(event, recommended_biomarkers=bios, now=NOW)
    assert out.recommended_biomarkers == bios


def test_engine_tolerates_missing_everything():
    # No type, no onset, no metadata — must not raise.
    event = _event()
    out = compute_insights(event, now=NOW)
    assert out.current_phase is None
    assert out.is_overdue is False
    assert out.days_since_onset is None
    assert out.upcoming_milestones == []

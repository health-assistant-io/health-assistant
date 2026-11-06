"""Calendar mapping law (ADR-0052 §6 / plan 52 P2).

Pure unit tests — no DB, no network. Pins:

* uid stability and the all-day exclusive end (``date + 1 day``);
* the redaction ladder — ``neutral`` (default) never leaks the title,
  notes, organization or category detail; ``none`` publishes a bare block;
  ``full`` publishes the user-visible detail and nothing beyond it;
* clinical events: timed when onset/resolved carry times, all-day
  otherwise, no projection without an onset;
* occurrences map to day blocks (no invented duration);
* mapping is deterministic: the same record hashes identically.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.extensions.cloud.cloud_calendar import (
    CalendarEvent,
    event_hash,
    event_to_dict,
    parse_ics,
    to_ics,
)
from app.services.calendar.mapping import (
    NEUTRAL_SUMMARY,
    NONE_SUMMARY,
    clinical_event_event,
    event_uid,
    examination_event,
    occurrence_event,
)

# Nasty clinical content — must never reach a remote event unless the user
# explicitly opts into `full`.
TITLE = "Rheumatology follow-up — Dr. Feld"
NOTES = "Biopsy scheduled; HIV panel ordered; home address changed to Teststraße 12"
ORG = "Klinikum Innenstadt"
TYPE_NAME = "Migraine"


def _exam(**overrides):
    base = {
        "id": uuid4(),
        "examination_date": datetime(2026, 11, 10).date(),
        "notes": NOTES,
        "patient_notes": None,
        "updated_at": datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _event(**overrides):
    base = {
        "id": uuid4(),
        "title": TITLE,
        "description": NOTES,
        "onset_date": datetime(2026, 11, 3, 9, 30, tzinfo=UTC),
        "resolved_date": datetime(2026, 11, 5, 18, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# uid law
# ---------------------------------------------------------------------------


def test_uids_are_stable_and_namespaced():
    exam_id, event_id, occ_id = uuid4(), uuid4(), uuid4()
    assert event_uid("examination", exam_id) == f"health:examination:{exam_id}"
    assert event_uid("clinical_event", event_id) == f"health:clinical-event:{event_id}"
    assert event_uid("occurrence", occ_id) == f"health:occurrence:{occ_id}"
    # stable across calls
    assert event_uid("examination", exam_id) == event_uid("examination", exam_id)


def test_examination_maps_all_day_with_exclusive_end():
    projection = examination_event(_exam(), redaction="neutral")
    assert projection is not None
    event = projection.event
    assert event.all_day is True
    assert str(event.start) == "2026-11-10"
    # canonical exclusive end: +1 day
    assert str(event.end) == "2026-11-11"
    assert projection.local_kind == "examination"


# ---------------------------------------------------------------------------
# redaction ladder
# ---------------------------------------------------------------------------


def test_marker_present_at_every_redaction_level_and_is_not_phi() -> None:
    """The E8 marker is attribution, never detail: it rides all levels."""
    from app.extensions.cloud.cloud_calendar import is_attributable

    exam = SimpleNamespace(
        id=uuid4(),
        examination_date=date(2026, 11, 10),
        patient_notes="very private notes",
        notes="clinician notes",
    )
    for level in ("none", "neutral", "full"):
        projection = examination_event(exam, redaction=level, category_name="Cardiology")
        assert projection is not None
        event = projection.event
        assert "NEURONECTION" in event.categories, level
        assert is_attributable(event, namespaces=("health:",)) is True
        # the marker leaks nothing
        assert "private" not in str(event.categories).lower()
        assert "Cardiology" not in str(event.categories) or level == "full"

    # a stranger's event is never attributable
    foreign = CalendarEvent(
        uid="x",
        start=date(2026, 11, 2),
        end=date(2026, 11, 3),
        all_day=True,
        summary="Cardiology follow-up",
    )
    assert is_attributable(foreign, namespaces=("health:",)) is False


def test_neutral_never_leaks_title_notes_or_organization():
    projection = examination_event(
        _exam(),
        redaction="neutral",
        category_name="Rheumatology",
        organization_name=ORG,
    )
    dumped = repr(event_to_dict(projection.event)) + to_ics([projection.event])
    for secret in (TITLE, NOTES, ORG, "Rheumatology", "Biopsy", "Feld"):
        assert secret not in dumped, f"neutral leaked {secret!r}"
    assert projection.event.summary == NEUTRAL_SUMMARY
    assert projection.event.description == ""
    assert projection.event.location == ""
    # E8: the family marker is attribution, not detail — present at every level
    assert projection.event.categories == ("NEURONECTION",)
    assert projection.event.private is True


def test_none_publishes_only_a_time_block():
    projection = examination_event(
        _exam(), redaction="none", category_name="Rheumatology", organization_name=ORG
    )
    event = projection.event
    assert event.summary == NONE_SUMMARY
    assert (
        event.description == "" and event.location == "" and event.categories == ("NEURONECTION",)
    )
    dumped = repr(event_to_dict(event))
    for secret in (TITLE, NOTES, ORG, NEUTRAL_SUMMARY):
        assert secret not in dumped


def test_full_publishes_the_opted_in_detail():
    projection = examination_event(
        _exam(), redaction="full", category_name="Rheumatology", organization_name=ORG
    )
    event = projection.event
    assert event.summary == "Rheumatology"
    assert event.location == ORG
    assert event.description == NOTES


def test_full_roundtrips_through_ics_and_parses_back():
    projection = examination_event(
        _exam(), redaction="full", category_name="Rheumatology", organization_name=ORG
    )
    parsed = parse_ics(to_ics([projection.event]))
    assert len(parsed) == 1
    back = parsed[0]
    assert back.summary == "Rheumatology"
    assert back.location == ORG
    assert back.all_day is True


def test_clinical_event_timed_when_onset_and_resolved_carry_times():
    projection = clinical_event_event(_event(), redaction="neutral", type_name=TYPE_NAME)
    event = projection.event
    assert event.all_day is False
    assert event.start == datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    assert event.end == datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
    assert event.summary == NEUTRAL_SUMMARY
    assert TYPE_NAME not in repr(event_to_dict(event))


def test_clinical_event_all_day_when_resolved_missing_or_not_after_onset():
    for resolved in (None, datetime(2026, 11, 3, 9, 30, tzinfo=UTC)):
        projection = clinical_event_event(_event(resolved_date=resolved), redaction="neutral")
        event = projection.event
        assert event.all_day is True
        assert str(event.start) == "2026-11-03"
        assert str(event.end) == "2026-11-04"  # exclusive


def test_clinical_event_without_onset_has_no_projection():
    assert clinical_event_event(_event(onset_date=None), redaction="neutral") is None


def test_occurrence_maps_to_a_day_block():
    occ = SimpleNamespace(
        id=uuid4(),
        occurred_at=datetime(2026, 11, 4, 14, 0, tzinfo=timezone(timedelta(hours=1))),
        title="Episode",
        notes="intensity 7",
    )
    projection = occurrence_event(occ, redaction="neutral")
    event = projection.event
    assert event.all_day is True
    assert str(event.start) == "2026-11-04"
    assert str(event.end) == "2026-11-05"
    assert event.summary == NEUTRAL_SUMMARY
    assert "intensity 7" not in repr(event_to_dict(event))


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_mapping_is_deterministic_for_content_hash():
    shared_id = uuid4()
    first = examination_event(_exam(id=shared_id), redaction="neutral")
    second = examination_event(_exam(id=shared_id), redaction="neutral")
    assert event_hash(first.event) == event_hash(second.event)
    assert first.event.uid == second.event.uid


def test_unknown_redaction_profile_is_refused():
    import pytest

    with pytest.raises(ValueError):
        examination_event(_exam(), redaction="diplomatic")

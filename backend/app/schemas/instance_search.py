"""Pydantic schemas for the unified instance search dispatcher.

A search ``hit`` is a uniform, denormalized projection of a patient-scoped
record (examination, medication, observation, document, clinical event,
allergy, vaccine) — enough to render a picker result row without a second
fetch. The generic frontend ``InstancePicker`` consumes exactly this shape.

See ``the design notes`` (Phase 2).
"""

from pydantic import BaseModel, ConfigDict


class InstanceSearchHit(BaseModel):
    """One search result, entity-agnostic."""

    model_config = ConfigDict(from_attributes=False)

    # The entity type — matches ``InstanceType`` on the frontend.
    type: str
    id: str
    label: str
    subtitle: str | None = None
    # ISO 8601 date string (the record's primary date), or None.
    date: str | None = None


class InstanceSearchResponse(BaseModel):
    results: list[InstanceSearchHit]

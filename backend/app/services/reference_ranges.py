"""Stratified biomarker reference-range resolution (audit B9 / F3).

``BiomarkerDefinition`` historically carried a single global
``reference_range_min``/``max``. That makes ``relative_score`` and the
status badge unreliable for anyone outside the "default" demographic — a
range normal for a 60-year-old female is not normal for a 25-year-old male.

The fix (audit B9) is a child table ``biomarker_reference_ranges`` holding
0..* ranges each scoped by sex / age window / unit (mirroring FHIR
``Observation.referenceRange`` with ``appliesTo`` + ``age``). This module
provides the resolution + scoring helpers consumed by the analytics/trends
path and the extraction pipeline.

Resolution is specificity-ranked: among all rows whose (sex, age-window,
unit) constraints are satisfied by the patient, the row constraining the
*most* dimensions wins. When no stratified row matches, the resolver falls
back to the biomarker's legacy global range so existing behaviour is
preserved (the ~30 display sites that read the global range keep working).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import BiomarkerDefinition, BiomarkerReferenceRange


@dataclass(frozen=True)
class ResolvedRange:
    """A resolved reference range + where it came from (for debugging/UX)."""

    low: Optional[float]
    high: Optional[float]
    text: Optional[str] = None
    # "stratified" = matched a biomarker_reference_ranges row;
    # "definition"  = fell back to the biomarker's legacy global range.
    source: str = "definition"

    @property
    def is_complete(self) -> bool:
        return self.low is not None and self.high is not None


def compute_relative_score(value: float, low: Optional[float], high: Optional[float]) -> Optional[float]:
    """Position of ``value`` within [low, high] as a clamped [0.0, 1.0] float.

    Returns ``0.5`` for an incomplete (one-sided) range, and ``None`` when
    there is no range at all. Mirrors the contract used by the OCR pipeline
    (``app.ai.pipeline.persistence``) and the integrations ``ObservationBuilder``
    so every write surface scores identically.
    """
    if value is None:
        return None
    if low is not None and high is not None and high > low:
        return max(0.0, min(1.0, (value - low) / (high - low)))
    if low is not None or high is not None:
        return 0.5
    return None


def _matches(row: BiomarkerReferenceRange, sex, age, unit_id) -> bool:
    """Does ``row`` apply to the given (sex, age, unit) context?

    A NULL dimension on the row means "any value" for that axis. Conversely, a
    row that *constrains* a dimension only applies when the patient's value for
    that dimension is KNOWN and satisfies the constraint — an unknown patient
    age/sex/unit cannot be assumed to fall inside a stratified window, so such
    rows do not match (and the resolver falls through to a less-specific row or
    the global range)."""
    if row.sex is not None:
        if sex is None or row.sex != sex:
            return False
    if row.age_min is not None or row.age_max is not None:
        if age is None:
            return False
        if row.age_min is not None and age < row.age_min:
            return False
        if row.age_max is not None and age > row.age_max:
            return False
    if row.unit_id is not None:
        if unit_id is None or str(row.unit_id) != str(unit_id):
            return False
    return True


def _specificity(row: BiomarkerReferenceRange) -> int:
    """Higher = more specific. Sex=1, unit=1, bounded age window=2 (both
    bounds) / 1 (single bound). Breaks ties toward the narrowest population."""
    score = 0
    if row.sex is not None:
        score += 1
    if row.unit_id is not None:
        score += 1
    if row.age_min is not None:
        score += 1
    if row.age_max is not None:
        score += 1
    return score


def _pick_best(rows, sex, age, unit_id) -> Optional[BiomarkerReferenceRange]:
    """Choose the most-specific applicable row, or None."""
    best: Optional[BiomarkerReferenceRange] = None
    best_score = -1
    for row in rows:
        if not _matches(row, sex, age, unit_id):
            continue
        score = _specificity(row)
        if score > best_score:
            best, best_score = row, score
    return best


def pick_reference_range(
    biomarker: BiomarkerDefinition,
    rows,
    *,
    sex=None,
    age: Optional[float] = None,
    unit_id=None,
) -> Optional[ResolvedRange]:
    """In-memory resolution from already-loaded ``rows`` (no DB hit).

    Use this in hot paths (e.g. the analytics trends loop) where the
    biomarker's ``reference_ranges`` collection is already eager-loaded via
    ``lazy="selectin"`` — it avoids one query per biomarker. Falls back to the
    biomarker's legacy global range when no stratified row matches.
    """
    best = _pick_best(rows, sex, age, unit_id)
    if best is not None:
        return ResolvedRange(
            low=best.low, high=best.high, text=best.text, source="stratified"
        )
    if biomarker.reference_range_min is not None or biomarker.reference_range_max is not None:
        return ResolvedRange(
            low=biomarker.reference_range_min,
            high=biomarker.reference_range_max,
            source="definition",
        )
    return None


async def resolve_reference_range(
    db: AsyncSession,
    biomarker: BiomarkerDefinition,
    *,
    sex=None,
    age: Optional[float] = None,
    unit_id=None,
) -> Optional[ResolvedRange]:
    """Resolve the best reference range for ``biomarker`` given patient context.

    Loads the biomarker's stratified rows and picks the most-specific match.
    Falls back to the biomarker's legacy global ``reference_range_min``/``max``
    when no stratified row applies, so callers get a range whenever the
    biomarker defines one.

    Args:
        db: async session.
        biomarker: the ``BiomarkerDefinition`` (or any object exposing
            ``id``/``reference_range_min``/``reference_range_max``).
        sex: a ``Gender`` enum value (or its string value) for the patient.
        age: patient age in years.
        unit_id: the unit UUID the value is expressed in (e.g. the
            observation's ``raw_unit_id``).

    Returns a :class:`ResolvedRange`, or ``None`` if the biomarker has no
    range at all.
    """
    result = await db.execute(
        select(BiomarkerReferenceRange).where(
            BiomarkerReferenceRange.biomarker_id == biomarker.id
        )
    )
    rows = result.scalars().all()
    return pick_reference_range(biomarker, rows, sex=sex, age=age, unit_id=unit_id)


async def resolve_for_patient(
    db: AsyncSession,
    biomarker: BiomarkerDefinition,
    patient,
    *,
    unit_id=None,
) -> Optional[ResolvedRange]:
    """Convenience wrapper: derive sex/age from a ``Patient`` ORM row.

    ``patient`` may be ``None`` (→ unstratified resolution, falls back to the
    global range), which keeps call sites simple when the patient isn't loaded.
    """
    sex = age = None
    if patient is not None:
        sex = getattr(patient, "gender", None)
        age = getattr(patient, "age", None)
    return await resolve_reference_range(db, biomarker, sex=sex, age=age, unit_id=unit_id)

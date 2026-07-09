"""Relation-type reference metadata — the single source of truth for the
human-facing description of each ``ConceptRelationType``.

The enum itself (``app/models/enums.py:ConceptRelationType``) carries only the
wire value. The label / description / icon / grouping that the UI (and AI tools)
need to *explain* a relation live here as static reference data — the "seed" for
relation-type semantics. ``GET /catalogs/relation-types`` exposes it.

Icons are lucide names (rendered as SVG by the frontend ``DynamicIcon``), the
same convention catalog-type UI metadata uses (``registrations.py`` icon field).
Keep the keys in sync with the ``ConceptRelationType`` members.
"""

from __future__ import annotations

from typing import Any

from app.models.enums import ConceptRelationType

# Group ordering for the UI dropdown sections.
GROUP_STRUCTURAL = "Structural / classification"
GROUP_MEDICAL = "Medical knowledge"


class RelationTypeMeta:
    """Static metadata for one relation type (not a Pydantic model — pure data)."""

    __slots__ = ("value", "label", "group", "description", "icon")

    def __init__(self, value: str, label: str, group: str, description: str, icon: str):
        self.value = value
        self.label = label
        self.group = group
        self.description = description
        self.icon = icon

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "label": self.label,
            "group": self.group,
            "description": self.description,
            "icon": {"type": "lucide", "value": self.icon},
        }


# fmt: off
# Ordered to match the enum's two groups (structural first, then medical).
_RELATION_META: dict[ConceptRelationType, RelationTypeMeta] = {
    # --- structural / classification ----------------------------------------
    ConceptRelationType.MEMBER_OF: RelationTypeMeta(
        "MEMBER_OF", "Member of", GROUP_STRUCTURAL,
        "Belongs to a group or panel — e.g. a biomarker is a member of a lab panel.",
        "Layers",
    ),
    ConceptRelationType.HAS_SPECIALTY: RelationTypeMeta(
        "HAS_SPECIALTY", "Has specialty", GROUP_STRUCTURAL,
        "A role or actor holds a medical specialty — e.g. a doctor specializes in cardiology.",
        "Award",
    ),
    ConceptRelationType.CLASSIFIED_AS: RelationTypeMeta(
        "CLASSIFIED_AS", "Classified as", GROUP_STRUCTURAL,
        "An instance of a class — e.g. a drug classified as an antibiotic.",
        "Tag",
    ),
    ConceptRelationType.EXAMINES: RelationTypeMeta(
        "EXAMINES", "Examines", GROUP_STRUCTURAL,
        "A specialty or role clinically assesses a concept — e.g. cardiology examines the heart.",
        "Stethoscope",
    ),
    ConceptRelationType.IMAGES: RelationTypeMeta(
        "IMAGES", "Images", GROUP_STRUCTURAL,
        "A modality or procedure depicts a structure — e.g. MRI images the brain.",
        "Scan",
    ),
    ConceptRelationType.PERFORMS: RelationTypeMeta(
        "PERFORMS", "Performs", GROUP_STRUCTURAL,
        "A role or actor carries out a procedure or action.",
        "Hand",
    ),
    ConceptRelationType.ORDERS: RelationTypeMeta(
        "ORDERS", "Orders", GROUP_STRUCTURAL,
        "A role or actor requests a test or procedure — e.g. a clinician orders a lab panel.",
        "ClipboardList",
    ),
    ConceptRelationType.LOCATED_IN: RelationTypeMeta(
        "LOCATED_IN", "Located in", GROUP_STRUCTURAL,
        "Spatial or anatomical containment — e.g. the thyroid is located in the neck.",
        "MapPin",
    ),
    ConceptRelationType.PART_OF: RelationTypeMeta(
        "PART_OF", "Part of", GROUP_STRUCTURAL,
        "Whole-to-part composition — e.g. a lobe is part of a lung.",
        "Share2",
    ),

    # --- anatomy hierarchy (migrated from AnatomyRelationType) -----------
    ConceptRelationType.BRANCH_OF: RelationTypeMeta(
        "BRANCH_OF", "Branch of", GROUP_STRUCTURAL,
        "Vascular or neural branching — e.g. the left coronary artery is a branch of the ascending aorta.",
        "GitBranch",
    ),
    ConceptRelationType.DRAINS_INTO: RelationTypeMeta(
        "DRAINS_INTO", "Drains into", GROUP_STRUCTURAL,
        "Lymphatic or venous drainage — e.g. a vein drains into a larger vessel.",
        "ArrowDownToLine",
    ),
    ConceptRelationType.ARTICULATES_WITH: RelationTypeMeta(
        "ARTICULATES_WITH", "Articulates with", GROUP_STRUCTURAL,
        "Joint formation between two bones — e.g. the femur articulates with the tibia.",
        "Link",
    ),
    ConceptRelationType.INNERVATED_BY: RelationTypeMeta(
        "INNERVATED_BY", "Innervated by", GROUP_STRUCTURAL,
        "Nerve supply to a structure — e.g. the diaphragm is innervated by the phrenic nerve.",
        "Cable",
    ),
    ConceptRelationType.SUPPLIED_BY: RelationTypeMeta(
        "SUPPLIED_BY", "Supplied by", GROUP_STRUCTURAL,
        "Blood supply to a structure — e.g. the heart is supplied by the coronary arteries.",
        "Droplet",
    ),
    ConceptRelationType.CONTINUOUS_WITH: RelationTypeMeta(
        "CONTINUOUS_WITH", "Continuous with", GROUP_STRUCTURAL,
        "Direct anatomical continuity — e.g. the stomach is continuous with the duodenum.",
        "Minus",
    ),

    # --- semantic / medical knowledge --------------------------------------
    ConceptRelationType.AFFECTS: RelationTypeMeta(
        "AFFECTS", "Affects", GROUP_MEDICAL,
        "A substance or agent acts on a body structure or function — e.g. a drug affects an organ or system.",
        "Zap",
    ),
    ConceptRelationType.TREATS: RelationTypeMeta(
        "TREATS", "Treats", GROUP_MEDICAL,
        "A therapy, drug, or procedure is used to manage a condition — drug → disease.",
        "Pill",
    ),
    ConceptRelationType.INDICATES: RelationTypeMeta(
        "INDICATES", "Indicates", GROUP_MEDICAL,
        "A finding or biomarker is evidence of a condition — e.g. high troponin indicates cardiac injury.",
        "Stethoscope",
    ),
    ConceptRelationType.CONTRAINDICATES: RelationTypeMeta(
        "CONTRAINDICATES", "Contraindicates", GROUP_MEDICAL,
        "A condition or factor makes a treatment unsafe — e.g. a penicillin allergy contraindicates penicillin.",
        "Ban",
    ),
    ConceptRelationType.PREVENTS: RelationTypeMeta(
        "PREVENTS", "Prevents", GROUP_MEDICAL,
        "An intervention stops or reduces the chance of a condition — e.g. a vaccine prevents a disease.",
        "ShieldCheck",
    ),
    ConceptRelationType.CORRELATES_WITH: RelationTypeMeta(
        "CORRELATES_WITH", "Correlates with", GROUP_MEDICAL,
        "A statistical or clinical association between two concepts, without implying cause.",
        "GitCompare",
    ),
    ConceptRelationType.CAUSED_BY: RelationTypeMeta(
        "CAUSED_BY", "Caused by", GROUP_MEDICAL,
        "A condition results from an agent or factor — e.g. an infection is caused by a pathogen.",
        "Bug",
    ),
    ConceptRelationType.MONITORS: RelationTypeMeta(
        "MONITORS", "Monitors", GROUP_MEDICAL,
        "A biomarker or test tracks the status or progress of a condition over time.",
        "Gauge",
    ),
    ConceptRelationType.RISK_OF: RelationTypeMeta(
        "RISK_OF", "Risk of", GROUP_MEDICAL,
        "A factor increases the likelihood of a condition — e.g. smoking is a risk of lung disease.",
        "AlertCircle",
    ),
    ConceptRelationType.SCREENS_FOR: RelationTypeMeta(
        "SCREENS_FOR", "Screens for", GROUP_MEDICAL,
        "A test checks for a condition, usually at an early or asymptomatic stage.",
        "ScanLine",
    ),
}
# fmt: on


def list_relation_types() -> list[dict[str, Any]]:
    """All relation types with their metadata, grouped + ordered for the UI.

    Returns ``[{"value", "label", "group", "description", "icon"}, ...]``.
    Every ``ConceptRelationType`` member is guaranteed an entry; a missing
    entry raises at import time (below) so it can't silently slip to prod.
    """
    return [_RELATION_META[rt].to_dict() for rt in ConceptRelationType]


# Fail fast at import if the enum and the registry drift apart.
_missing = [rt for rt in ConceptRelationType if rt not in _RELATION_META]
if _missing:
    raise RuntimeError(
        f"RELATION_TYPE_META is missing entries for: "
        f"{', '.join(rt.value for rt in _missing)}. "
        f"Add them in app/catalogs/relation_types.py."
    )

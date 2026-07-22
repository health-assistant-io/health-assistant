"""AI-assisted catalog definitions: biomarker and medication.

Extracted from ``AIAssistanceService`` (Phase 6c). Each handler uses
``llm.with_structured_output(<Pydantic>)`` + ``ChatPromptTemplate`` and returns
``{"suggested_data": ..., "success": True}``.

``AIAssistanceService`` keeps thin delegate methods (``_define_biomarker`` etc.)
so the dispatcher and direct test calls (``svc._define_biomarker``) continue to
work. These handlers do not query the DB, so the delegates intentionally do not
pass ``self.db`` — this preserves the ``AIAssistanceService.__new__(...)`` call
shape used in tests.
"""

import logging
from typing import Any, Dict, Optional

from langchain_core.prompts import ChatPromptTemplate

from app.ai.schemas.assistance import (
    AnatomyGraphDefinitionOutput,
    BiomarkerDefinitionOutput,
    MedicationDefinitionOutput,
)
from app.utils.prompt_guard import DEFENSE_PREAMBLE

logger = logging.getLogger(__name__)


async def define_biomarker(
    llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """AI-driven biomarker definition builder"""
    system_prompt = """You are a medical data expert assisting in creating a new biomarker definition for a clinical catalog.
        
        You MUST provide a valid JSON object with the following fields:
        - name: Full official medical name.
        - category: Clinical category.
        - unit_symbol: Preferred unit symbol.
        - coding_system: The medical coding system to use (e.g., "loinc", "custom"). Try to map standard labs to "loinc".
        - code: The specific code from the coding system (e.g., "2345-7").
        - aliases: List of synonyms.
        - reference_range_min: Typical lower bound (float).
        - reference_range_max: Typical upper bound (float).
        - is_telemetry: Boolean. True only if this metric is tracked continuously via IoT/wearables (e.g. heart rate, steps).
        - info: Detailed clinical explanation.
        
        Suggested values are mandatory for all fields even if the user only provides a name.
        """

    structured_llm = llm.with_structured_output(BiomarkerDefinitionOutput)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "Fully define this biomarker: {user_input}"),
        ]
    )

    chain = prompt | structured_llm
    result = await chain.ainvoke({"user_input": user_input})

    # Use logger.debug so diagnostic output only surfaces when DEBUG
    # logging is enabled and never leaks to stdout in production deployments.
    logger.debug("AI biomarker definition generated for %r", user_input)

    return {"suggested_data": result.model_dump(), "success": True}


async def define_medication(
    llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """AI-driven medication definition builder"""
    system_prompt = """You are a medical pharmacology expert assisting in creating a new medication entry for a clinical catalog.
        
        You MUST provide a valid JSON object with the following fields:
        - name: Full generic or brand name of the medication.
        - description: A brief but informative overview of the drug and its class.
        - indications: Main medical uses for this drug.
        - dosage_info: Standard dosage forms and typical instructions.
        - contraindications: Major reasons why this drug should NOT be used.
        - side_effects: A list of common adverse reactions.
        
        Suggested values are mandatory for all fields even if the user only provides a name.
        """

    structured_llm = llm.with_structured_output(MedicationDefinitionOutput)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "Fully define this medication: {user_input}"),
        ]
    )

    chain = prompt | structured_llm
    result = await chain.ainvoke({"user_input": user_input})

    # Routed through logger.debug so diagnostic output is gated on DEBUG.
    logger.debug("AI medication definition generated for %r", user_input)

    return {"suggested_data": result.model_dump(), "success": True}


async def define_anatomy_graph(
    llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """AI-driven anatomy graph expansion.

    Generates a list of nodes (``AnatomyImportNode``) and edges
    (``AnatomyImportEdge``) describing the anatomical hierarchy of the structure
    named in ``user_input``. The output is fed into the same JSON import pipeline
    as ``POST /api/v1/anatomy/import``.

    When ``context['existing']`` carries a snapshot of structures/edges that
    already exist for the target (populated by the ``propose_anatomy_graph_*
    `` tool's pre-flight search), the prompt instructs the LLM to generate only
    the MISSING detail — referencing existing nodes as edge endpoints without
    recreating them — so the import upserts gaps instead of duplicating.
    """
    existing = (context or {}).get("existing") if isinstance(context, dict) else None
    dedup_block = _existing_anatomy_block(existing)

    system_prompt = f"""You are an expert anatomical ontologist assisting in building a modular graph database of the human body.

    The user wants to generate the anatomical hierarchy and components for a specific body part, organ, or system.
    You must output a list of nodes (AnatomyImportNode) and edges (AnatomyImportEdge) that represent this anatomical structure.

    Guidelines:
    - The `slug` must be globally unique, kebab-case (e.g., 'left-ventricle').
    - `is_custom` should be true.
    - Ensure all `source_slug` and `target_slug` in the edges exist in the nodes you provide, OR refer to major known systems (like 'heart', 'brain', 'cardiovascular-system').
    - Be highly accurate with relation types (PART_OF, BRANCH_OF, DRAINS_INTO, etc.).
    {dedup_block}"""

    chain = ChatPromptTemplate.from_messages(
        [
            ("system", DEFENSE_PREAMBLE + system_prompt),
            ("human", "Generate the anatomy graph for: {input}"),
        ]
    ) | llm.with_structured_output(AnatomyGraphDefinitionOutput)
    try:
        result = await chain.ainvoke({"input": user_input})
        return {"suggested_data": result.model_dump(), "success": True}
    except Exception as e:
        logger.error("Error defining anatomy graph: %s", e)
        return {"success": False, "message": str(e)}


def _existing_anatomy_block(existing: Optional[Dict[str, Any]]) -> str:
    """Build the 'do not duplicate' instruction block for the anatomy-graph
    generation prompt from a pre-flight existing-graph snapshot. Returns an
    empty string when no snapshot is present (generate from scratch)."""
    if not existing or not isinstance(existing, dict):
        return ""
    node_slugs = existing.get("node_slugs") or []
    edges = existing.get("edges") or []
    if not node_slugs:
        return ""
    parts = [
        "\n    - The following structures ALREADY EXIST for this target. Do NOT "
        "recreate them as new nodes — you MAY reference them as edge endpoints "
        "(e.g. a new 'left-atrium' node PART_OF the existing 'heart').:"
    ]
    for slug in node_slugs[:80]:  # cap to keep the prompt bounded
        parts.append(f"      • {slug}")
    if edges:
        parts.append(
            "    - These edges ALREADY EXIST — do NOT recreate them:"
        )
        for e in edges[:80]:
            parts.append(
                f"      • {e.get('source_slug')} -> {e.get('target_slug')} "
                f"({e.get('relation_type')})"
            )
    parts.append(
        "    - Focus on generating the MISSING anatomical detail (sub-structures, "
        "supply, drainage, innervation) that fills gaps in the existing graph."
    )
    return "\n".join(parts)

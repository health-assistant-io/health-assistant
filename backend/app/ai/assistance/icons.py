"""AI-assisted category icon suggestion + generation.

Extracted from ``AIAssistanceService`` (Phase 6c). ``suggest_category_icon``
returns Lucide icon names; ``generate_category_icon`` produces a sanitized SVG.
Both use ``llm.with_structured_output(<Pydantic>)``.

``AIAssistanceService`` keeps thin delegate methods. These handlers do not query
the DB, so the delegates intentionally do not pass ``self.db``.
"""
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from app.ai.schemas.assistance import (
    CategoryIconGenerationOutput,
    CategoryIconSuggestionOutput,
)
from app.utils.svg import sanitize_svg

logger = logging.getLogger(__name__)


async def suggest_category_icon(
    llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """Suggest Lucide icons based on category name/description"""
    system_prompt = """You are a UI expert for a medical application. 
        The user is creating a medical examination category (e.g., 'Hematology', 'Radiology').
        Suggest 5-8 appropriate Lucide icon names that represent this category.
        
        Rules:
        - Return ONLY the Lucide icon names in PascalCase (e.g., 'Activity', 'Droplet', 'Stethoscope').
        - Ensure the icons are available in the Lucide library.
        - Prioritize medical or health-related icons.
        """

    structured_llm = llm.with_structured_output(CategoryIconSuggestionOutput)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "Suggest icons for this medical category: {user_input}"),
        ]
    )

    chain = prompt | structured_llm
    result = await chain.ainvoke({"user_input": user_input})

    return {"suggested_icons": result.suggested_icons, "success": True}


async def generate_category_icon(
    llm,
    user_input: str,
    reference_image: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate or refine a custom Lucide-style SVG icon"""
    instruction = context.get("instruction") if context else None
    previous_svg = context.get("previous_svg") if context else None

    system_prompt = """You are a minimalist UI designer specializing in medical iconography and SVG vector art.
        Your task is to generate or refine a clean, modern SVG icon that matches the aesthetic of the Lucide library.

        STYLE GUIDELINES:
        - Minimalistic but Accurate: Capture the essential shape of the medical concept or organ.
        - Lucide Aesthetic: Icons should be lightweight, "open", and consist of clean line work.
        - Professional: Ensure the icon is clearly recognizable for the clinical specialty.
        - Fills: You may use fill="currentColor" for specific paths if it helps define the shape (e.g. solid lungs or a filled heart), but keep it simple.

        TECHNICAL SPECIFICATIONS:
        - Viewport: 24x24 (strictly).
        - Stroke Width: 2px (strictly).
        - Colors: Use 'currentColor' for stroke and/or fill.
        - Line Quality: Use 'round' for both linecap and linejoin.
        - Transparency: The SVG background is naturally transparent.
        - Padding: Keep the icon content within the 2 to 22 range.

        OUTPUT REQUIREMENTS:
        - Return ONLY the raw SVG code within the 'svg_content' field.
        - Ensure valid XML with xmlns="http://www.w3.org/2000/svg" and viewBox="0 0 24 24".
        - Optimized: No metadata, comments, titles, or nested tags.
        """

    if previous_svg:
        human_prompt = f"Refine this existing medical icon for: '{user_input}'.\n\nCURRENT SVG:\n{previous_svg}"
        if instruction:
            human_prompt += f"\n\nREFINE INSTRUCTION: {instruction}"
        else:
            human_prompt += "\n\nPlease improve the visual representation while maintaining the style."
    else:
        human_prompt = f"Create a professional, minimalistic, and accurate medical icon for: '{user_input}'."
        if instruction:
            human_prompt += f"\n\nUser Instructions: {instruction}"
        else:
            human_prompt += "\n\nDesign a simple but recognizable visual for this medical specialty using clean paths."

    if reference_image:
        human_prompt += "\n\nI have provided a reference image. Please use it as a guide for the icon structure/metaphor, but convert it to the requested minimalistic SVG line style."

    messages = [SystemMessage(content=system_prompt)]

    human_content: List[Dict[str, Any]] = [{"type": "text", "text": human_prompt}]
    if reference_image:
        # Ensure it has the correct prefix
        if not reference_image.startswith("data:"):
            # Assume it's a jpeg base64 if no prefix
            reference_image = f"data:image/jpeg;base64,{reference_image}"

        human_content.append(
            {"type": "image_url", "image_url": {"url": reference_image}}
        )

    messages.append(HumanMessage(content=human_content))

    structured_llm = llm.with_structured_output(CategoryIconGenerationOutput)
    result = await structured_llm.ainvoke(messages)

    # Sanitize and optimize the generated SVG
    svg = sanitize_svg(result.svg_content)

    return {
        "svg_content": svg,
        "justification": result.justification,
        "success": True,
    }

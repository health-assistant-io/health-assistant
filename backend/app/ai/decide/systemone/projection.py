"""Question/tool projection onto the System One wire shapes.

Ported verbatim in shape from study's Phase 3 Python reference (which
ported it from the desktop reference).
"""

import re
from typing import Any

from ..types import (
    DecisionAnswer,
    DecisionCall,
    DecisionCapability,
    DecisionOutcome,
    DecisionQuestion,
    DecisionToolSchema,
    DecisionUsage,
    sanitize_confidence,
)
from .client import TypeSafeQuestion, TypeSafeResult

TOOL_CHOICE_ID = "__tool__"
NONE_OPTION = "__none__"

CATALOG_ARG_PATTERN = re.compile(r"^(area|entity|entity_id|device|room|name)$", re.IGNORECASE)


def is_catalog_arg_name(arg: str) -> bool:
    return bool(CATALOG_ARG_PATTERN.match(arg))


def _properties_of(tool: DecisionToolSchema) -> dict[str, dict[str, Any]]:
    parameters = tool.parameters or {}
    properties = parameters.get("properties")
    if isinstance(properties, dict):
        return {
            str(name): (schema if isinstance(schema, dict) else {})
            for name, schema in properties.items()
        }
    return {}


def _usage_of(result: TypeSafeResult) -> DecisionUsage | None:
    if result.usage is None:
        return None
    return DecisionUsage(
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


def build_tool_dispatch_questions(
    tools: list[DecisionToolSchema],
    catalog_entities: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, TypeSafeQuestion]:
    criteria: dict[str, str | None] = {
        NONE_OPTION: "The user is chatting, or asking something that needs no tool."
    }
    for tool in tools:
        criteria[tool.name] = tool.description or None
    questions: dict[str, TypeSafeQuestion] = {
        TOOL_CHOICE_ID: TypeSafeQuestion(
            type=DecisionCapability.CHOICE,
            instructions="What is the user asking the assistant to do?",
            criteria=criteria,
        )
    }
    for tool in tools:
        entity_ids: tuple[str, ...] = ()
        if tool.app_id and catalog_entities is not None:
            entity_ids = catalog_entities.get(tool.app_id, ())
        for arg, schema in _properties_of(tool).items():
            question_id = f"{tool.name}.{arg}"
            schema_type = str(schema.get("type") or "")
            enum_values = schema.get("enum")
            if isinstance(enum_values, list):
                questions[question_id] = TypeSafeQuestion(
                    type=DecisionCapability.CHOICE,
                    instructions=f"Which {arg} does the user want for {tool.name}?",
                    criteria={str(value): None for value in enum_values},
                )
            elif schema_type == "boolean":
                questions[question_id] = TypeSafeQuestion(
                    type=DecisionCapability.BOOLEAN_GATE,
                    instructions=f"Does the user want {arg} for {tool.name}?",
                )
            elif is_catalog_arg_name(arg) and entity_ids:
                questions[question_id] = TypeSafeQuestion(
                    type=DecisionCapability.CHOICE,
                    instructions=f"Which known {arg} is the user referring to for {tool.name}?",
                    criteria=dict.fromkeys(entity_ids),
                )
    return questions


def _resolved_model_of(result: TypeSafeResult) -> str | None:
    model = result.model
    return model if model else None


def tool_dispatch_outcome(
    result: TypeSafeResult,
    tools: list[DecisionToolSchema],
    model_id: str,
) -> DecisionOutcome:
    tool_answer = result.answers.get(TOOL_CHOICE_ID)
    resolved_model = _resolved_model_of(result)
    if (
        tool_answer is None
        or tool_answer.type != DecisionCapability.CHOICE
        or tool_answer.choice == NONE_OPTION
    ):
        confidence = 0.0
        if tool_answer is not None and tool_answer.type == DecisionCapability.CHOICE:
            confidence = sanitize_confidence(tool_answer.confidence)
        return DecisionOutcome(
            engine=model_id,
            calls=(),
            confidence=confidence,
            resolved_model=resolved_model,
        )
    tool = next((candidate for candidate in tools if candidate.name == tool_answer.choice), None)
    if tool is None:
        return DecisionOutcome(
            engine=model_id, calls=(), confidence=0.0, resolved_model=resolved_model
        )
    args: dict[str, Any] = {}
    confidence = tool_answer.confidence or 0.0
    for arg in _properties_of(tool):
        answer = result.answers.get(f"{tool.name}.{arg}")
        if answer is None:
            continue
        if answer.type == DecisionCapability.CHOICE and answer.choice is not None:
            args[arg] = answer.choice
            confidence = min(confidence, answer.confidence or 0.0)
        elif answer.type == DecisionCapability.BOOLEAN_GATE and answer.noul is not None:
            if answer.noul >= 0.5:
                args[arg] = True
            confidence = min(confidence, max(answer.noul, 1 - answer.noul))
    return DecisionOutcome(
        engine=model_id,
        calls=(DecisionCall(tool=tool.name, args=args),),
        confidence=sanitize_confidence(confidence),
        usage=_usage_of(result),
        resolved_model=resolved_model,
    )


def build_question_set(questions: list[DecisionQuestion]) -> dict[str, TypeSafeQuestion]:
    built: dict[str, TypeSafeQuestion] = {}
    for question in questions:
        if question.type == DecisionCapability.CHOICE:
            built[question.id] = TypeSafeQuestion(
                type=DecisionCapability.CHOICE,
                instructions=question.instructions,
                criteria=dict(question.options or {}),
            )
        elif question.type == DecisionCapability.SCORE:
            built[question.id] = TypeSafeQuestion(
                type=DecisionCapability.SCORE,
                instructions=question.instructions,
                levels=tuple(question.levels or ()),
            )
        else:
            criteria = question.criteria
            built[question.id] = TypeSafeQuestion(
                type=DecisionCapability.BOOLEAN_GATE,
                instructions=question.instructions,
                true_criteria=criteria[0] if criteria else None,
                false_criteria=criteria[1] if criteria else None,
            )
    return built


def question_outcome(
    result: TypeSafeResult,
    questions: list[DecisionQuestion],
    model_id: str,
) -> DecisionOutcome:
    answers: dict[str, DecisionAnswer] = {}
    certainties: list[float] = []
    for question in questions:
        answer = result.answers.get(question.id)
        if answer is None:
            continue
        answers[question.id] = answer
        if answer.type == DecisionCapability.BOOLEAN_GATE and answer.noul is not None:
            certainties.append(max(answer.noul, 1 - answer.noul))
        elif answer.confidence is not None:
            certainties.append(answer.confidence)
    confidence = min(certainties) if certainties else 0.0
    return DecisionOutcome(
        engine=model_id,
        calls=(),
        confidence=sanitize_confidence(confidence),
        answers=answers,
        usage=_usage_of(result),
        resolved_model=result.model or None,
    )

"""Per-parser payload schema validation for inbound webhooks (plan 8 W4).

A webhook is a hostile-input surface: an unauthenticated-shaped body that
passes HMAC verification is still attacker JSON. This module turns "parse and
hope" into a typed contract:

* **shape** — the root must be a JSON object, nesting depth and node counts are
  bounded, and every declared section must be a list of objects;
* **types** — a declared numeric field carrying a string (or a bool, which
  Python models as ``int``) is a typed rejection, not a silent skip;
* **size** — a per-payload byte budget far below the HTTP route cap, so a
  parser never walks a payload the platform would rather reject.

Rejections raise :class:`WebhookPayloadError` (charter code ``protocol``;
``invalid`` for a bad parser configuration). The webhook route persists them as
typed failure rows — silence is never an outcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.extensions.errors import ExtensionError

#: Parser-level payload budget. The HTTP route caps the raw body at 25 MiB
#: (audit 2026-08 M4); no parser consumes anything near that, so a delivery
#: over this size is a typed rejection instead of a long walk.
MAX_PAYLOAD_BYTES = 1024 * 1024
#: Deepest accepted nesting (a hostile payload can nest to blow the stack).
MAX_DEPTH = 16
#: Total nodes the validator will walk before rejecting.
MAX_NODES = 50_000
#: Longest accepted list for one section.
MAX_SECTION_ITEMS = 10_000


class WebhookPayloadError(ExtensionError):
    """A payload that does not satisfy its parser's schema (``protocol``)."""

    def __init__(self, detail: str) -> None:
        super().__init__("protocol", detail)


class WebhookConfigError(ExtensionError):
    """A parser configuration problem the delivery cannot fix (``invalid``)."""

    def __init__(self, detail: str) -> None:
        super().__init__("invalid", detail)


@dataclass(frozen=True)
class SectionSpec:
    """Declared shape of one payload section.

    ``fields`` maps a field name to a primitive kind: ``number`` (int/float,
    never bool), ``text`` (str), ``object`` (dict), ``list`` (list) or ``any``.
    Unknown fields are ignored — providers add fields without breaking
    deliveries (forward compatibility); declared fields are enforced.
    """

    fields: dict[str, str] = field(default_factory=dict)


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "text":
        return isinstance(value, str)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "list":
        return isinstance(value, list)
    return True


def _measure(payload: Any) -> None:
    """Bound size, depth and node count before any parser touches the value."""
    try:
        serialized = json.dumps(payload, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as exc:
        raise WebhookPayloadError("Payload is not JSON-serializable.") from exc
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise WebhookPayloadError(f"Payload exceeds the {MAX_PAYLOAD_BYTES}-byte parser budget.")

    stack: list[tuple[Any, int]] = [(payload, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise WebhookPayloadError(f"Payload exceeds the {MAX_NODES}-node budget.")
        if depth > MAX_DEPTH:
            raise WebhookPayloadError(f"Payload nesting exceeds {MAX_DEPTH} levels.")
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)


def validate_payload(payload: Any, *, parser_id: str) -> dict[str, Any]:
    """Validate the root shape (object, bounded size/depth). Returns the payload."""
    if not isinstance(payload, dict):
        raise WebhookPayloadError(
            f"{parser_id}: payload must be a JSON object, got {type(payload).__name__}."
        )
    _measure(payload)
    return payload


def validate_sections(
    payload: dict[str, Any],
    sections: dict[str, SectionSpec],
    *,
    parser_id: str,
) -> None:
    """Validate the declared sections of an already root-validated payload."""
    for name, spec in sections.items():
        if name not in payload:
            continue
        value = payload[name]
        if value is None:
            continue
        if not isinstance(value, list):
            raise WebhookPayloadError(
                f"{parser_id}: section '{name}' must be a list, got {type(value).__name__}."
            )
        if len(value) > MAX_SECTION_ITEMS:
            raise WebhookPayloadError(
                f"{parser_id}: section '{name}' exceeds {MAX_SECTION_ITEMS} items."
            )
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise WebhookPayloadError(
                    f"{parser_id}: section '{name}[{index}]' must be an object, "
                    f"got {type(item).__name__}."
                )
            for field_name, kind in spec.fields.items():
                if field_name not in item or item[field_name] is None:
                    continue
                if not _type_ok(item[field_name], kind):
                    raise WebhookPayloadError(
                        f"{parser_id}: section '{name}[{index}].{field_name}' must be {kind}."
                    )


def validate_object_section(
    payload: dict[str, Any],
    name: str,
    *,
    parser_id: str,
    fields: dict[str, str] | None = None,
) -> None:
    """Validate a single object-typed section (e.g. ``custom_mapping_json``)."""
    if name not in payload or payload[name] is None:
        return
    value = payload[name]
    if not isinstance(value, dict):
        raise WebhookPayloadError(
            f"{parser_id}: '{name}' must be an object, got {type(value).__name__}."
        )
    for field_name, kind in (fields or {}).items():
        if field_name in value and not _type_ok(value[field_name], kind):
            raise WebhookPayloadError(f"{parser_id}: '{name}.{field_name}' must be {kind}.")

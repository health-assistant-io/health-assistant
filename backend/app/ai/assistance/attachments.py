"""Image-attachment validation & multimodal content building for chat.

The agentic chatbot supports vision input: a user may attach one or more
images (lab report scans, wound photos, charts, ...) alongside their text
question. This module is the single place that:

  * normalizes the wire format (``data:<mime>;base64,<payload>`` data URLs),
  * enforces per-image size + count + MIME-type limits (defense in depth —
    the frontend also validates, but the backend is the trust boundary),
  * builds the LangChain multimodal content payload that vision models
    (OpenAI-compatible ``ChatOpenAI``) consume.

Wire format & transport
-----------------------
Images travel as RFC 2397 data URLs inside the JSON request body
(``AIAssistanceRequest.images: List[str]``) and are persisted inside the
``ChatMessage.content`` JSONB (``{"text": ..., "images": [...]}``) so a
reloaded session reconstructs them. LangChain ``HumanMessage`` accepts a list
of typed content blocks (``{"type": "text"|"image_url", ...}``) which is the
OpenAI vision message schema; we emit exactly that.

Security
--------
Image bytes bypass the text prompt-injection guard (it scans text only). The
HITL wall remains the structural defence for clinical writes. Size/MIME/count
limits here prevent abuse (huge payloads, non-image content, token blowups).
"""

import base64
import binascii
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings


class AllowedImageMime(str, Enum):
    """MIME types accepted as chat image attachments.

    Restricted to common web image formats that vision models understand.
    SVG is intentionally excluded (it is not rasterized and is a vector for
    script/XSS payloads); the dedicated SVG icon generator sanitizes SVG
    separately via :mod:`app.utils.svg`.
    """

    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"
    GIF = "image/gif"


_ALLOWED_MIME = {m.value for m in AllowedImageMime}

# ``data:<mime>;base64,<payload>`` — capture mime + payload groups.
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", re.DOTALL
)


class ImageValidationError(ValueError):
    """Raised when a chat image attachment fails validation.

    Subclasses :class:`ValueError` so it surfaces as a user-facing
    ``error_type="guard"`` SSE message via ``_classify_stream_error`` in the
    chat-stream endpoint (no raw SDK text leaks).
    """


def _decode_size(payload_b64: str) -> int:
    """Approximate decoded byte length of a base64 string without full decode."""
    cleaned = "".join(payload_b64.split())
    # Every 4 base64 chars ~ 3 bytes; padding handled roughly (close enough
    # for a size guard — exact length is validated again post-decode).
    return (len(cleaned) * 3) // 4


def validate_image_data_url(data_url: str) -> str:
    """Validate & normalize a single chat image data URL.

    Returns the normalized ``data:<mime>;base64,<payload>`` string (single
    line, no whitespace). Raises :class:`ImageValidationError` on any problem
    (bad format, disallowed MIME, oversized payload, non-base64 content).
    """
    if not isinstance(data_url, str) or not data_url:
        raise ImageValidationError("Empty image attachment.")

    match = _DATA_URL_RE.match(data_url.strip())
    if not match:
        raise ImageValidationError(
            "Invalid image format. Expected a data:image/...;base64,... URL."
        )

    mime = match.group("mime").lower()
    payload = match.group("data").strip()

    if mime not in _ALLOWED_MIME:
        allowed = ", ".join(sorted(_ALLOWED_MIME))
        raise ImageValidationError(
            f"Unsupported image type '{mime}'. Allowed: {allowed}."
        )

    if _decode_size(payload) > settings.AI_CHAT_MAX_IMAGE_BYTES:
        limit_mb = settings.AI_CHAT_MAX_IMAGE_BYTES / (1024 * 1024)
        raise ImageValidationError(
            f"Image exceeds the {limit_mb:.0f} MiB per-image limit."
        )

    # Confirm the payload is genuinely base64-decodable (catch corruption /
    # disguised content) without keeping the bytes around.
    try:
        base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageValidationError("Image payload is not valid base64.") from exc

    return f"data:{mime};base64,{payload}"


def validate_chat_images(images: Optional[List[str]]) -> List[str]:
    """Validate a list of chat image attachments.

    Enforces the per-request image count limit and validates each entry via
    :func:`validate_image_data_url`. Returns the list of normalized data URLs
    (empty list when ``images`` is falsy). Raises
    :class:`ImageValidationError` on the first failure (fail fast — the whole
    turn is rejected so partial multimodal context is never built).
    """
    if not images:
        return []

    if len(images) > settings.AI_CHAT_MAX_IMAGES:
        raise ImageValidationError(
            f"Too many images. The limit is {settings.AI_CHAT_MAX_IMAGES} per message."
        )

    return [validate_image_data_url(img) for img in images]


def build_multimodal_content(
    text: str, images: Optional[List[str]] = None
) -> Union[str, List[Dict[str, Any]]]:
    """Build the LangChain ``HumanMessage`` content for a chat turn.

    Returns a plain ``str`` when there are no images (the common, cheapest
    path — identical to legacy behaviour). When one or more validated images
    are present, returns a list of typed content blocks in the OpenAI vision
    message schema:

        [
            {"type": "text", "text": <user text>},
            {"type": "image_url", "image_url": {"url": <data url>}},
            ...
        ]

    ``images`` MUST already be validated by :func:`validate_chat_images`.
    """
    if not images:
        return text or ""

    blocks: List[Dict[str, Any]] = [{"type": "text", "text": text or ""}]
    for data_url in images:
        blocks.append({"type": "image_url", "image_url": {"url": data_url}})
    return blocks


def has_images(content_json: Optional[Dict[str, Any]]) -> bool:
    """True when a persisted ``ChatMessage.content`` JSONB carries images."""
    if not content_json or not isinstance(content_json, dict):
        return False
    images = content_json.get("images")
    return bool(images) and isinstance(images, list)

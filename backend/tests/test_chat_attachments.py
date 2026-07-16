"""Tests for chat image-attachment validation & multimodal content building.

Covers ``app.ai.assistance.attachments``:
  * MIME-type whitelist enforcement (allowed formats accepted, SVG/others rejected).
  * Per-image byte-size limit enforcement.
  * Per-request image-count limit enforcement.
  * Malformed (non-data-URL / non-base64) payload rejection.
  * ``build_multimodal_content`` returns plain str when no images and the
    OpenAI vision content-block list otherwise.
  * ``has_images`` correctly inspects persisted ``ChatMessage.content``.
"""
import base64

import pytest


def _png_data_url(payload_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(payload_bytes).decode()


def _jpeg_data_url(payload_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(payload_bytes).decode()


# ---------------------------------------------------------------------------
# validate_chat_images
# ---------------------------------------------------------------------------


def test_valid_png_accepted():
    from app.ai.assistance.attachments import validate_chat_images

    url = _png_data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
    result = validate_chat_images([url])
    assert result == [url]


def test_allowed_mime_types_accepted():
    from app.ai.assistance.attachments import validate_chat_images

    for mime in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        url = f"data:{mime};base64," + base64.b64encode(b"data").decode()
        assert len(validate_chat_images([url])) == 1


def test_svg_rejected():
    from app.ai.assistance.attachments import (
        ImageValidationError,
        validate_chat_images,
    )

    with pytest.raises(ImageValidationError, match="Unsupported image type"):
        validate_chat_images(["data:image/svg+xml;base64,PHN2Zz4="])


def test_malformed_data_url_rejected():
    from app.ai.assistance.attachments import (
        ImageValidationError,
        validate_chat_images,
    )

    with pytest.raises(ImageValidationError, match="Invalid image format"):
        validate_chat_images(["not-a-data-url"])


def test_non_base64_payload_rejected():
    from app.ai.assistance.attachments import (
        ImageValidationError,
        validate_chat_images,
    )

    with pytest.raises(ImageValidationError, match="not valid base64"):
        validate_chat_images(["data:image/png;base64,@@@not b64@@@"])


def test_empty_input_returns_empty_list():
    from app.ai.assistance.attachments import validate_chat_images

    assert validate_chat_images(None) == []
    assert validate_chat_images([]) == []


def test_count_limit_enforced():
    from app.ai.assistance.attachments import (
        ImageValidationError,
        validate_chat_images,
    )
    from app.core.config import settings

    url = _png_data_url(b"x" * 20)
    with pytest.raises(ImageValidationError, match="Too many images"):
        validate_chat_images([url] * (settings.AI_CHAT_MAX_IMAGES + 1))


def test_size_limit_enforced(monkeypatch):
    from app.ai.assistance.attachments import (
        ImageValidationError,
        validate_chat_images,
    )
    from app.ai.assistance import attachments as att_mod

    # Lower the limit to a few bytes so a small payload trips it without
    # having to construct a multi-megabyte string in memory.
    monkeypatch.setattr(att_mod.settings, "AI_CHAT_MAX_IMAGE_BYTES", 10)
    url = _png_data_url(b"x" * 200)
    with pytest.raises(ImageValidationError, match="exceeds"):
        validate_chat_images([url])


# ---------------------------------------------------------------------------
# build_multimodal_content
# ---------------------------------------------------------------------------


def test_content_plain_string_without_images():
    from app.ai.assistance.attachments import build_multimodal_content

    assert build_multimodal_content("hello", None) == "hello"
    assert build_multimodal_content("hello", []) == "hello"


def test_content_multimodal_blocks_with_images():
    from app.ai.assistance.attachments import build_multimodal_content

    url = _png_data_url(b"img")
    content = build_multimodal_content("describe this", [url])
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "describe this"}
    assert content[1] == {"type": "image_url", "image_url": {"url": url}}


def test_content_multimodal_multiple_images_preserve_order():
    from app.ai.assistance.attachments import build_multimodal_content

    urls = [_png_data_url(b"a"), _jpeg_data_url(b"b"), _png_data_url(b"c")]
    content = build_multimodal_content("multi", urls)
    image_blocks = [b for b in content if b["type"] == "image_url"]
    assert [b["image_url"]["url"] for b in image_blocks] == urls


def test_content_empty_text_with_images():
    from app.ai.assistance.attachments import build_multimodal_content

    url = _png_data_url(b"img")
    content = build_multimodal_content("", [url])
    assert content[0] == {"type": "text", "text": ""}


# ---------------------------------------------------------------------------
# has_images
# ---------------------------------------------------------------------------


def test_has_images_detects_attachments():
    from app.ai.assistance.attachments import has_images

    assert has_images(None) is False
    assert has_images({}) is False
    assert has_images({"text": "hi"}) is False
    assert has_images({"text": "hi", "images": []}) is False
    assert has_images({"text": "hi", "images": [_png_data_url(b"x")]}) is True

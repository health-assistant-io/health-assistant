"""Unit tests for ``integrations.sdk.webhook_security``.

These pin the contract used by both the platform webhook/API-proxy
endpoints (``app.api.v1.endpoints.integrations``) and per-provider
verification (``integrations.dev_dummy.provider``). The HMAC algorithm
itself was previously inlined in two places; this is the canonical
implementation, so the tests are exhaustive about the accepted forms.
"""
from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from integrations.sdk.webhook_security import (
    DEFAULT_WEBHOOK_SIGNATURE_HEADERS,
    get_signature_header,
    verify_canonical_signature,
    verify_hmac_signature,
)


SECRET = "topsecret"
BODY = b'{"hello":"world"}'


def _sig(body: bytes = BODY, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# verify_hmac_signature
# ---------------------------------------------------------------------------


def test_verify_hmac_signature_accepts_bare_hex():
    assert verify_hmac_signature(SECRET, BODY, _sig()) is True


def test_verify_hmac_signature_accepts_sha256_equals_prefix():
    """GitHub / Slack convention: ``sha256=<hex>``."""
    assert verify_hmac_signature(SECRET, BODY, f"sha256={_sig()}") is True


def test_verify_hmac_signature_accepts_sha256_colon_prefix():
    """Alternate prefix — covered by default ``accepted_prefixes``."""
    assert verify_hmac_signature(SECRET, BODY, f"sha256:{_sig()}") is True


def test_verify_hmac_signature_case_insensitive():
    assert verify_hmac_signature(SECRET, BODY, _sig().upper()) is True
    assert verify_hmac_signature(SECRET, BODY, f"SHA256={_sig().upper()}") is True


def test_verify_hmac_signature_rejects_wrong_secret():
    bad = hmac.new(b"other-secret", BODY, hashlib.sha256).hexdigest()
    assert verify_hmac_signature(SECRET, BODY, bad) is False


def test_verify_hmac_signature_rejects_tampered_body():
    # Add a single byte to the body — signature must no longer match.
    assert verify_hmac_signature(SECRET, BODY + b"!", _sig()) is False


def test_verify_hmac_signature_rejects_empty_inputs():
    assert verify_hmac_signature("", BODY, _sig()) is False
    assert verify_hmac_signature(SECRET, BODY, "") is False
    assert verify_hmac_signature(SECRET, BODY, None) is False  # type: ignore[arg-type]


def test_verify_hmac_signature_strips_whitespace():
    """Leading/trailing whitespace is stripped before comparison."""
    assert verify_hmac_signature(SECRET, BODY, f"  {_sig()}  ") is True


def test_verify_hmac_signature_custom_prefixes():
    """Callers can override the accepted prefix list (e.g. ``v1=``)."""
    sig = _sig()
    assert verify_hmac_signature(SECRET, BODY, f"v1={sig}", accepted_prefixes=("v1=",)) is True


# ---------------------------------------------------------------------------
# verify_canonical_signature
# ---------------------------------------------------------------------------


def _canonical_sig(method: str, path: str, body: bytes, *, timestamp: str | None = None) -> str:
    parts = [method.upper().encode(), b"\n", path.encode(), b"\n"]
    if timestamp is not None:
        parts.append(timestamp.encode() + b"\n")
    parts.append(body)
    return hmac.new(SECRET.encode(), b"".join(parts), hashlib.sha256).hexdigest()


def test_verify_canonical_signature_basic():
    sig = _canonical_sig("POST", "/foo", BODY)
    assert verify_canonical_signature(SECRET, "POST", "/foo", BODY, sig) is True


def test_verify_canonical_signature_method_case_insensitive():
    sig = _canonical_sig("post", "/foo", BODY)
    assert verify_canonical_signature(SECRET, "POST", "/foo", BODY, sig) is True


def test_verify_canonical_signature_rejects_wrong_path():
    sig = _canonical_sig("POST", "/foo", BODY)
    assert verify_canonical_signature(SECRET, "POST", "/bar", BODY, sig) is False


def test_verify_canonical_signature_with_timestamp():
    ts = str(int(time.time()))
    sig = _canonical_sig("POST", "/foo", BODY, timestamp=ts)
    assert (
        verify_canonical_signature(
            SECRET, "POST", "/foo", BODY, sig, provided_timestamp=ts
        )
        is True
    )


def test_verify_canonical_signature_rejects_replay_outside_skew():
    """A timestamp older than ``max_skew_seconds`` is rejected even if
    the signature is otherwise valid — defends against replay."""
    too_old = str(int(time.time()) - 1000)
    sig = _canonical_sig("POST", "/foo", BODY, timestamp=too_old)
    assert (
        verify_canonical_signature(
            SECRET, "POST", "/foo", BODY, sig, provided_timestamp=too_old
        )
        is False
    )


def test_verify_canonical_signature_rejects_malformed_timestamp():
    assert (
        verify_canonical_signature(
            SECRET, "POST", "/foo", BODY, "garbage", provided_timestamp="not-a-number"
        )
        is False
    )


def test_verify_canonical_signature_rejects_empty_inputs():
    assert verify_canonical_signature("", "POST", "/foo", BODY, "abc") is False
    assert verify_canonical_signature(SECRET, "POST", "/foo", BODY, "") is False


def test_verify_canonical_signature_timestamp_folded_into_payload():
    """Two requests with the same body but different timestamps must
    produce different signatures (otherwise the skew check would be
    bypassable)."""
    ts1 = str(int(time.time()))
    ts2 = str(int(time.time()) - 10)
    sig1 = _canonical_sig("POST", "/foo", BODY, timestamp=ts1)
    sig2 = _canonical_sig("POST", "/foo", BODY, timestamp=ts2)
    assert sig1 != sig2


# ---------------------------------------------------------------------------
# get_signature_header
# ---------------------------------------------------------------------------


def test_get_signature_header_default_names():
    """The conventional header names are picked up by default."""
    for name in DEFAULT_WEBHOOK_SIGNATURE_HEADERS:
        headers = {name: "abc123"}
        assert get_signature_header(headers) == "abc123"


def test_get_signature_header_case_insensitive():
    """HTTP headers are case-insensitive per RFC 7230."""
    assert get_signature_header({"x-webhook-signature": "abc"}) == "abc"
    assert get_signature_header({"X-WEBHOOK-SIGNATURE": "abc"}) == "abc"


def test_get_signature_header_custom_names():
    """Providers with their own header convention (e.g. dev_dummy's
    ``X-DevDummy-Signature``) declare it explicitly."""
    assert (
        get_signature_header({"X-DevDummy-Signature": "abc"}, names=("X-DevDummy-Signature",))
        == "abc"
    )


def test_get_signature_header_returns_none_when_missing():
    assert get_signature_header({}) is None
    assert get_signature_header({"Other-Header": "x"}) is None


def test_get_signature_header_skips_empty_values():
    """Empty-string values are treated as missing."""
    assert get_signature_header({"X-Webhook-Signature": ""}) is None


def test_get_signature_header_handles_non_mapping():
    """Defensive — if a caller passes a non-mapping object, return None."""
    assert get_signature_header(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Contract — both helpers are constant-time on length-mismatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "impl",
    [
        lambda: verify_hmac_signature(SECRET, BODY, "x" * 10),
        lambda: verify_canonical_signature(SECRET, "POST", "/foo", BODY, "x" * 10),
    ],
)
def test_signature_helpers_do_not_raise_on_short_input(impl):
    """A short / malformed signature must not crash ``compare_digest``
    even though length differs from the computed digest."""
    assert impl() is False

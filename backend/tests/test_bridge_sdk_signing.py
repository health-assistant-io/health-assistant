"""Tests for the Python bridge client SDK signing helper.

Verifies the client-side ``sign_request`` produces a signature that the
server-side ``verify_canonical_signature`` accepts — the round-trip contract
that lets a signed ``/map`` or ``/sync`` request through the bridge HMAC gate.
"""
from __future__ import annotations

import sys
import os

# Make the python-sdk importable without pip install.
_SDK_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "integrations",
    "health_assistant_bridge", "python-sdk",
)
sys.path.insert(0, _SDK_DIR)

import pytest  # noqa: E402

from health_assistant_bridge.signing import sign_request  # noqa: E402
from integrations.sdk.webhook_security import verify_canonical_signature  # noqa: E402


SECRET = "topsecret"
BODY = b'{"records":[]}'
PATH = "/sync"
METHOD = "POST"
TS = 1234567890


def test_sign_request_produces_valid_headers():
    headers = sign_request(SECRET, METHOD, PATH, BODY, timestamp=TS)
    assert "X-Api-Signature" in headers
    assert "X-Api-Timestamp" in headers
    assert headers["X-Api-Timestamp"] == str(TS)


def test_sign_request_accepted_by_server_verifier():
    """The signature the client produces must be accepted by the server-side
    verify_canonical_signature — the round-trip contract."""
    headers = sign_request(SECRET, METHOD, PATH, BODY, timestamp=TS)
    ok = verify_canonical_signature(
        SECRET, METHOD, PATH, BODY, headers["X-Api-Signature"],
        provided_timestamp=headers["X-Api-Timestamp"], max_skew_seconds=10**9,
    )
    assert ok is True


def test_sign_request_rejected_with_wrong_secret():
    headers = sign_request(SECRET, METHOD, PATH, BODY, timestamp=TS)
    ok = verify_canonical_signature(
        "wrong-secret", METHOD, PATH, BODY, headers["X-Api-Signature"],
        provided_timestamp=headers["X-Api-Timestamp"], max_skew_seconds=10**9,
    )
    assert ok is False


def test_sign_request_rejected_tampered_body():
    headers = sign_request(SECRET, METHOD, PATH, BODY, timestamp=TS)
    ok = verify_canonical_signature(
        SECRET, METHOD, PATH, BODY + b"!", headers["X-Api-Signature"],
        provided_timestamp=headers["X-Api-Timestamp"], max_skew_seconds=10**9,
    )
    assert ok is False


def test_sign_request_requires_secret():
    with pytest.raises(ValueError, match="api_secret is required"):
        sign_request("", METHOD, PATH, BODY)


def test_sign_request_method_case_normalised():
    """The client may pass lowercase; the server normalises to uppercase."""
    headers = sign_request(SECRET, "post", PATH, BODY, timestamp=TS)
    ok = verify_canonical_signature(
        SECRET, "POST", PATH, BODY, headers["X-Api-Signature"],
        provided_timestamp=headers["X-Api-Timestamp"], max_skew_seconds=10**9,
    )
    assert ok is True
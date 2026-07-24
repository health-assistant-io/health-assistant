"""HMAC request signing for the Health Assistant Bridge client SDKs.

Mirrors the server-side canonical form used by
``integrations.sdk.webhook_security.verify_canonical_signature``::

    canonical = METHOD + "\\n" + path + "\\n" + [timestamp + "\\n"] + raw_body

where ``timestamp`` is an integer epoch-second string folded into the MAC
(and checked against a ±5 min skew window on the server). The resulting
signature is sent as ``X-Api-Signature`` (hex) + ``X-Api-Timestamp``.

When the bridge instance has an ``api_secret`` configured, the client MUST
sign ``/map`` and ``/sync`` requests or the server rejects them with HTTP
400. ``/status`` is never signed. When no ``api_secret`` is set, the bridge
runs UUID-as-secret only (no signing needed).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict


def sign_request(
    secret: str,
    method: str,
    path: str,
    raw_body: bytes,
    *,
    timestamp: int | None = None,
) -> Dict[str, str]:
    """Return the ``X-Api-Signature`` / ``X-Api-Timestamp`` headers for a request.

    Args:
        secret: The plaintext ``api_secret`` configured on the bridge instance.
        method: HTTP method (``POST``/``GET``/…).
        path: The API path component *after* the integration id, with a
            leading ``/`` (e.g. ``"/sync"``).
        raw_body: The exact request body bytes that will be sent. The signature
            covers these bytes (not a re-serialized copy) so the caller must
            pass the same bytes to the HTTP layer.
        timestamp: Override the epoch-second timestamp (mainly for tests).
    """
    if not secret:
        raise ValueError("api_secret is required to sign a request.")
    ts = str(timestamp if timestamp is not None else int(time.time()))
    canonical = (
        method.upper().encode() + b"\n"
        + path.encode() + b"\n"
        + ts.encode() + b"\n"
        + (raw_body or b"")
    )
    digest = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return {"X-Api-Signature": digest, "X-Api-Timestamp": ts}
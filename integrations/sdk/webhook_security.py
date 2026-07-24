"""Reusable webhook / API-request signature verification helpers.

HMAC verification was previously inlined in two places:

* The platform endpoint layer
  (``app.api.v1.endpoints.integrations._verify_webhook_signature`` /
  ``_verify_api_signature``) — used by the generic webhook + API proxy
  routes.
* The ``dev_dummy`` provider
  (``integrations/dev_dummy/provider.py._verify_signature``) — used to
  validate per-instance ``X-DevDummy-Signature`` headers against the
  Fernet-encrypted ``webhook_secret`` config field.

Both implemented the same algorithm. This module is the single home
for it; both call sites should delegate here.

Module boundary: this module imports **only** from the Python stdlib.
It must not import from ``app.*`` — otherwise we'd create a circular
dependency (the endpoint imports from the SDK; the SDK must not import
from the endpoint).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Iterable, Mapping, Optional


__all__ = [
    "verify_hmac_signature",
    "verify_canonical_signature",
    "verify_stripe_signature",
    "get_signature_header",
    "DEFAULT_WEBHOOK_SIGNATURE_HEADERS",
    "STRIPE_SIGNATURE_HEADERS",
]


# Conventional header names providers/integrations are most likely to
# send. Lookup is case-insensitive (HTTP headers are case-insensitive
# per RFC 7230 §3.2).
DEFAULT_WEBHOOK_SIGNATURE_HEADERS: tuple[str, ...] = (
    "X-Webhook-Signature",
    "X-Webhook-Signature-256",
    "X-Hub-Signature-256",  # GitHub
)

# Stripe's signed-event header carries ``t=<epoch>,v1=<hex>`` and is
# replay-protected by folding the timestamp into the MAC.
STRIPE_SIGNATURE_HEADERS: tuple[str, ...] = ("Stripe-Signature",)


def _safe_compare(a: str, b: str) -> bool:
    """``hmac.compare_digest`` that returns ``False`` instead of raising on
    non-ASCII input.

    ``compare_digest`` raises ``TypeError`` when either argument contains
    non-ASCII characters (it only accepts ASCII strings, falling back to
    bytes). A client sending a garbage non-ASCII signature would otherwise
    turn into an uncaught 500 — treat it as "not verified".
    """
    try:
        return hmac.compare_digest(a, b)
    except TypeError:
        return False


def get_signature_header(
    headers: Mapping[str, str],
    *,
    names: Iterable[str] = DEFAULT_WEBHOOK_SIGNATURE_HEADERS,
) -> Optional[str]:
    """Return the first matching signature header value.

    HTTP headers are case-insensitive; we lowercase both sides for the
    lookup. Returns ``None`` if none of ``names`` are present.
    """
    if not hasattr(headers, "items"):
        return None
    # Fast path: build a lowercased view once. ``headers`` could be a
    # Starlette ``Headers`` instance (case-insensitive already) or a
    # plain dict (case-sensitive) — normalise so both work.
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in names:
        key = name.lower()
        if key in lowered and lowered[key]:
            return lowered[key]
    return None


def verify_hmac_signature(
    secret: str,
    raw_body: bytes,
    provided_signature: str,
    *,
    accepted_prefixes: tuple[str, ...] = ("sha256=", "sha256:"),
) -> bool:
    """Constant-time HMAC-SHA256 verification of a webhook payload.

    Supported conventions:

    * Bare hex digest (generic): ``<hex>``
    * Prefixed hex digest (GitHub, Slack): ``sha256=<hex>`` /
      ``sha256:<hex>`` — the prefix is stripped before comparison.

    .. note::

        This bare-MAC form has **no replay protection** — any captured valid
        payload (from logs, a proxy, a misconfigured CDN) can be replayed
        indefinitely. Use :func:`verify_stripe_signature` (Stripe's
        ``t=<epoch>,v1=<hex>`` scheme) or :func:`verify_canonical_signature`
        (timestamped) when you need replay defense.

    Returns ``True`` iff the computed HMAC matches ``provided_signature``
    (after prefix stripping + case normalisation) using
    :func:`hmac.compare_digest`. Returns ``False`` when either argument
    is empty (callers should treat this as "not verified" and reject
    the request).

    The ``secret`` is the **plaintext** signing key — callers decrypt
    the Fernet-encrypted ``webhook_secret`` config field before calling
    this helper.
    """
    if not secret or not provided_signature:
        return False

    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    provided = provided_signature.strip().lower()
    for prefix in accepted_prefixes:
        if provided.startswith(prefix):
            provided = provided[len(prefix):]
            break

    return _safe_compare(computed, provided.strip())


def verify_canonical_signature(
    secret: str,
    method: str,
    path: str,
    raw_body: bytes,
    provided_signature: str,
    *,
    provided_timestamp: Optional[str] = None,
    max_skew_seconds: int = 300,
) -> bool:
    """Constant-time HMAC-SHA256 verification of an inbound two-way API call.

    The signed canonical string is::

        <METHOD>\n<path>\n[<timestamp>\n]<raw_body>

    When ``provided_timestamp`` is supplied:

    * It must be an integer epoch second.
    * The request is rejected if ``abs(now - ts) > max_skew_seconds``.
    * The timestamp is folded into the signed payload so a captured
      signature cannot be replayed after the skew window.

    Used by the generic API proxy
    (``/{domain}/api/{integration_id}/{path}``) when an integration
    configures ``api_secret`` in its ``user_config`` — replacing the
    UUID-as-only-secret default.

    Returns ``True`` iff the computed HMAC matches ``provided_signature``
    AND (when timestamp is supplied) the timestamp is within the allowed
    skew window. Returns ``False`` when ``secret`` or
    ``provided_signature`` is empty, or when the timestamp is malformed.
    """
    if not secret or not provided_signature:
        return False

    canonical_parts: list[bytes] = [
        method.upper().encode("utf-8"),
        b"\n",
        path.encode("utf-8"),
        b"\n",
    ]
    if provided_timestamp:
        try:
            ts_int = int(provided_timestamp)
        except (ValueError, TypeError):
            return False
        now = int(time.time())
        if abs(now - ts_int) > max_skew_seconds:
            return False
        canonical_parts.append(provided_timestamp.encode("utf-8") + b"\n")
    canonical_parts.append(raw_body)
    canonical = b"".join(canonical_parts)

    computed = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return _safe_compare(computed, provided_signature.strip().lower())


def verify_stripe_signature(
    secret: str,
    payload: bytes,
    header_value: str,
    *,
    tolerance: int = 300,
) -> bool:
    """Verify a Stripe-style ``t=<epoch>,v1=<hex>`` webhook signature.

    Stripe's ``Stripe-Signature`` header is a comma-separated list of
    ``key=value`` pairs; the two that matter are:

    * ``t``   — the integer epoch second when Stripe signed the payload.
    * ``v1``  — ``HMAC-SHA256(secret, "{t}.{payload}")`` (hex).

    The timestamp is folded into the MAC **and** checked against a skew
    window (``tolerance`` seconds, default 300 = 5 min) so a captured
    signature can't be replayed after the window closes. This is the
    replay-protected counterpart to the bare :func:`verify_hmac_signature`.

    Returns ``True`` iff the header parses, the timestamp is within
    ``tolerance`` of now, and ``v1`` matches the recomputed MAC. Returns
    ``False`` on any malformation (missing ``t``/``v1``, non-integer ``t``,
    out-of-window timestamp, or an empty ``secret``).
    """
    if not secret or not header_value:
        return False

    timestamp: Optional[str] = None
    signatures: list[str] = []
    for part in header_value.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "t":
            timestamp = value
        elif key == "v1" and value:
            signatures.append(value)

    if timestamp is None or not signatures:
        return False
    try:
        ts_int = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(int(time.time()) - ts_int) > tolerance:
        return False

    signed = f"{timestamp}.".encode("utf-8") + payload
    computed = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    # Constant-time compare against every supplied v1 (Stripe may send
    # multiple for key rotation); accept if any matches.
    return any(_safe_compare(computed, sig.lower()) for sig in signatures)

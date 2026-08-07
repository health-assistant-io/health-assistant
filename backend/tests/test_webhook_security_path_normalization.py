"""Path-form tolerance tests for ``verify_canonical_signature``.

Regression guard for the silent auth-failure bug where the client SDKs sign
the path **with** a leading slash (``"/map"``) while the FastAPI
``{path:path}`` route capture yields it **without** one (``"map"``). Before
the fix the canonical strings differed (``POST\n/map\n...`` vs
``POST\nmap\n...``) so every signed request was rejected with 401 when
``api_secret`` was configured.

Post-fix contract pinned here:

1. A signature computed over ``"/map"`` verifies against a verifier called
   with ``"map"`` (and vice versa) — both forms are accepted.
2. Internal path segments and trailing slashes are NOT munged — only the
   leading slash is tolerated.
3. The tolerance is independent of the timestamp/replay behaviour.
4. The real Python SDK ``sign_request`` helper (imported end-to-end)
   verifies against the bare-path verifier call — the original bug scenario.
"""
import hashlib
import hmac

from integrations.sdk.webhook_security import verify_canonical_signature


def _sign(secret: str, method: str, path: str, body: bytes = b"", timestamp: int | None = None) -> str:
    """Mirror the canonical scheme used by the SDK ``sign_request`` helpers."""
    parts = [method.upper().encode(), b"\n", path.encode(), b"\n"]
    if timestamp is not None:
        parts.append(f"{timestamp}".encode() + b"\n")
    parts.append(body)
    return hmac.new(secret.encode(), b"".join(parts), hashlib.sha256).hexdigest()


def test_with_slash_signature_verifies_bare_path():
    """SDK signs ``/map``; endpoint passes ``map`` → must verify (the bug)."""
    secret = "topsecret"
    sig = _sign(secret, "POST", "/map", b'{"x":1}')
    assert verify_canonical_signature(
        secret, "POST", "map", b'{"x":1}', sig
    ) is True


def test_bare_signature_verifies_with_slash_path():
    """Inverse direction: bare-path signature, with-slash verifier call."""
    secret = "topsecret"
    sig = _sign(secret, "POST", "sync", b'{"x":1}')
    assert verify_canonical_signature(
        secret, "POST", "/sync", b'{"x":1}', sig
    ) is True


def test_same_form_still_verifies():
    """Sanity: signing and verifying with the identical form still works."""
    secret = "topsecret"
    for path in ("/map", "map", "/data/sub", "data/sub"):
        sig = _sign(secret, "GET", path, b"body")
        assert verify_canonical_signature(secret, "GET", path, b"body", sig) is True


def test_internal_segments_not_munged():
    """Only the leading slash is tolerated; internal ``/`` must round-trip."""
    secret = "topsecret"
    # ``map/sub`` signed must NOT verify against ``mapsub`` or ``map\\sub``.
    sig = _sign(secret, "POST", "map/sub", b"body")
    assert verify_canonical_signature(secret, "POST", "mapsub", b"body", sig) is False
    # And the nested form verifies on both leading-slash variants.
    assert verify_canonical_signature(secret, "POST", "/map/sub", b"body", sig) is True


def test_tampered_body_rejected_across_forms():
    """Body tampering must be rejected regardless of path form."""
    secret = "topsecret"
    sig = _sign(secret, "POST", "/map", b'{"x":1}')
    assert verify_canonical_signature(secret, "POST", "map", b'{"x":2}', sig) is False
    assert verify_canonical_signature(secret, "POST", "/map", b'{"x":2}', sig) is False


def test_tolerance_with_timestamp_replay_protection():
    """Tolerance + timestamp: signed with slash, verified bare, in-window."""
    import time

    secret = "topsecret"
    ts = int(time.time())  # in-window so the skew check doesn't reject
    sig = _sign(secret, "POST", "/map", b"body", timestamp=ts)
    assert verify_canonical_signature(
        secret, "POST", "map", b"body", sig, provided_timestamp=str(ts),
        max_skew_seconds=300,
    ) is True


def test_empty_path_forms():
    """Empty path: a signature over ``""`` verifies (no slash to toggle)."""
    secret = "topsecret"
    sig = _sign(secret, "POST", "", b"body")
    assert verify_canonical_signature(secret, "POST", "", b"body", sig) is True


# ---------------------------------------------------------------------------
# End-to-end: real Python SDK signer → verifier (the original bug scenario)
# ---------------------------------------------------------------------------

def test_real_sdk_sign_request_verifies_through_bare_path():
    """Import the actual SDK ``sign_request`` and feed it through the verifier
    exactly as the platform endpoint would. Pre-fix this was the failing
    production path: SDK signed ``/sync`` (with slash), endpoint captured
    ``sync`` (no slash) → 401 for every signed request.
    """
    # The SDK package ships its own ``sign_request``; import it directly so
    # this test fails if the SDK and verifier ever diverge again.
    import importlib.util
    import pathlib

    sdk_root = (
        pathlib.Path(__file__).resolve().parents[2]
        / "integrations" / "health_assistant_bridge" / "python-sdk"
    )
    spec = importlib.util.spec_from_file_location(
        "ha_bridge_signing", sdk_root / "health_assistant_bridge" / "signing.py"
    )
    assert spec and spec.loader, "SDK signing module not found"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sign_request = module.sign_request

    secret = "topsecret-topsecret"  # min 16 chars per the config-flow validator
    import time
    ts = int(time.time())
    body = b'{"metrics":[]}'
    headers = sign_request(secret, "POST", "/sync", body, timestamp=ts)

    assert "X-Api-Signature" in headers
    assert "X-Api-Timestamp" in headers

    # Endpoint captures path WITHOUT leading slash ("sync"), per FastAPI
    # ``{path:path}`` semantics. This is the exact production call shape.
    assert verify_canonical_signature(
        secret,
        "POST",
        "sync",                       # bare path — what the route captures
        body,
        headers["X-Api-Signature"],
        provided_timestamp=headers["X-Api-Timestamp"],
        max_skew_seconds=300,
    ) is True

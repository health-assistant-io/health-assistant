"""RFC 6238 TOTP + single-use recovery codes (plan 16 H5, identity-auth
§5.14/§20 as a health-owned kit extension).

The verifier interface is unchanged — TOTP slots into the login flow as a
second factor between password success and session issuance. Product
parameters (RFC 6238 defaults, the interoperability set every
authenticator app speaks):

* HMAC-SHA1, 6 digits, 30-second step, ``±1`` step drift window (a code
  stays valid one step either side of "now" — clock skew between the
  phone and the server without opening a brute-force lane).
* Secrets: 20 random bytes (160-bit), base32-encoded (RFC 4648, padding
  stripped — what authenticator apps expect).
* Provisioning via the standard ``otpauth://totp/...`` URI; the frontend
  renders the QR from the URI/secret (``qrcode.react``).

Recovery codes: 8 single-use codes from a confusion-safe alphabet
(``XXXX-XXXX``). At rest they are bcrypt-hashed exactly like passwords
(see ``app.core.security.get_password_hash``); the plaintext exists only
in the one-time enrollment response.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

# RFC 6238 interoperability parameters.
TOTP_ALGORITHM = "SHA1"
TOTP_DIGITS = 6
TOTP_STEP_SECONDS = 30
TOTP_DRIFT_STEPS = 1

# Secret entropy: 20 bytes (160 bits) — the RFC 4226 recommendation.
TOTP_SECRET_BYTES = 20

# Recovery codes: count + alphabet (no I/L/O/U/0/1 — human-transcribable).
RECOVERY_CODE_COUNT = 8
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def generate_totp_secret(nbytes: int = TOTP_SECRET_BYTES) -> str:
    """A fresh base32 TOTP secret (padding stripped, authenticator-style)."""
    import base64

    return base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def _hotp(key: bytes, counter: int, digits: int = TOTP_DIGITS) -> str:
    """RFC 4226 HOTP — the TOTP primitive (counter = unix_time // step)."""
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def normalize_code(code: str) -> str:
    """Strip the shapes humans type (spaces, dashes) and uppercase."""
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def totp_code_at(secret_b32: str, timestamp: float) -> str:
    """The expected 6-digit code for ``secret`` at ``timestamp`` (test
    vector hook — verification goes through :func:`verify_totp`)."""
    import base64

    padded = secret_b32 + "=" * (-len(secret_b32) % 4)
    key = base64.b32decode(padded, casefold=True)
    return _hotp(key, int(timestamp // TOTP_STEP_SECONDS))


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    at: Optional[float] = None,
    window: int = TOTP_DRIFT_STEPS,
) -> bool:
    """Constant-time TOTP check with ``±window`` drift steps.

    The presented value must be exactly 6 digits after normalization —
    padded/partial codes never match, and the comparison is
    ``hmac.compare_digest`` so a matching-length guess reveals nothing
    about how close it was.
    """
    presented = normalize_code(code)
    if len(presented) != TOTP_DIGITS or not presented.isdigit():
        return False
    now = at if at is not None else datetime.now(timezone.utc).timestamp()
    counter = int(now // TOTP_STEP_SECONDS)
    for drift in range(-window, window + 1):
        expected = totp_code_at(secret_b32, (counter + drift) * TOTP_STEP_SECONDS)
        if hmac.compare_digest(expected, presented):
            return True
    return False


def build_totp_uri(secret_b32: str, email: str, issuer: str) -> str:
    """The standard ``otpauth://`` provisioning URI (Google Authenticator
    key-uri format) — label ``issuer:email``, explicit parameters so
    exotic apps do not guess."""
    label = quote(f"{issuer}:{email}", safe=":")
    query = (
        f"secret={quote(secret_b32)}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm={TOTP_ALGORITHM}"
        f"&digits={TOTP_DIGITS}"
        f"&period={TOTP_STEP_SECONDS}"
    )
    return f"otpauth://totp/{label}?{query}"


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """``count`` fresh single-use codes in ``XXXX-XXXX`` form."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def canonical_recovery_code(code: str) -> str:
    """Normalize a typed code to the stored ``XXXX-XXXX`` form (accepts
    lowercase, spaces, and a missing dash)."""
    compact = normalize_code(code)
    if len(compact) != 8:
        return compact
    return f"{compact[:4]}-{compact[4:]}"

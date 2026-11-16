"""A software WebAuthn authenticator for the passkey test suites.

Implements just enough of WebAuthn Level 2 to drive the real server
ceremonies with cryptography instead of a browser: an ES256 (P-256) key,
``fmt="none"`` attestation, and DER-encoded assertions. Every field is
configurable so tests can exercise origin/RP mismatches, missing user
verification, replay and signature-counter regressions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


@dataclass
class VirtualAuthenticator:
    """One authenticator with a stable credential and counter."""

    rp_id: str = "localhost"
    origin: str = "http://localhost:3000"
    user_verified: bool = True
    user_present: bool = True
    backup_eligible: bool = False
    backup_state: bool = False
    sign_count: int = 0
    aaguid: bytes = field(default_factory=lambda: b"\x00" * 16)
    transports: list[str] = field(default_factory=lambda: ["internal"])

    def __post_init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        numbers = self._key.public_key().public_numbers()
        self._x = numbers.x.to_bytes(32, "big")
        self._y = numbers.y.to_bytes(32, "big")
        self.credential_id = hashlib.sha256(self._x + self._y).digest()[:32]
        self.user_handle = b""

    # --- shared pieces -----------------------------------------------------

    def _flags(self, *, attested: bool) -> int:
        flags = 0
        if self.user_present:
            flags |= FLAG_UP
        if self.user_verified:
            flags |= FLAG_UV
        if self.backup_eligible:
            flags |= FLAG_BE
            if self.backup_state:
                flags |= FLAG_BS
        if attested:
            flags |= FLAG_AT
        return flags

    def _cose_key(self) -> bytes:
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: self._x,
                -3: self._y,
            }
        )

    def _client_data(self, kind: str, challenge: str, *, origin: str | None = None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode("utf-8")

    # --- ceremonies --------------------------------------------------------

    def registration_credential(
        self,
        options: dict,
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        challenge: str | None = None,
        transports: list[str] | None = None,
    ) -> dict:
        """The JSON body for ``POST /me/mfa/passkeys/verify``."""
        challenge = challenge or str(options["challenge"])
        self.user_handle = b64url_decode(str(options["user"]["id"]))
        client_data = self._client_data("webauthn.create", challenge, origin=origin)
        rp_hash = hashlib.sha256((rp_id or str(options["rp"]["id"])).encode()).digest()
        auth_data = (
            rp_hash
            + bytes([self._flags(attested=True)])
            + struct.pack(">I", self.sign_count)
            + self.aaguid
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": transports if transports is not None else self.transports,
            },
        }

    def assertion(
        self,
        options: dict,
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        challenge: str | None = None,
        sign_count: int | None = None,
        user_verified: bool | None = None,
        with_user_handle: bool = True,
    ) -> dict:
        """The JSON body for a login/step-up verify call.

        ``sign_count`` overrides the stored counter (clone simulation);
        omitting it advances the authenticator by one.
        """
        challenge = challenge or str(options["challenge"])
        if user_verified is not None:
            previous = self.user_verified
            self.user_verified = user_verified
        try:
            flags = self._flags(attested=False)
        finally:
            if user_verified is not None:
                self.user_verified = previous
        counter = (self.sign_count + 1) if sign_count is None else int(sign_count)
        self.sign_count = counter
        rp_hash = hashlib.sha256((rp_id or str(options["rpId"])).encode()).digest()
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", counter)
        client_data = self._client_data("webauthn.get", challenge, origin=origin)
        signature = self._key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        response = {
            "clientDataJSON": b64url(client_data),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(signature),
        }
        if with_user_handle and self.user_handle:
            response["userHandle"] = b64url(self.user_handle)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": response,
        }

    def forge_signature(self, assertion: dict) -> dict:
        """Corrupt an assertion's signature (invalid-assertion test)."""
        raw = bytearray(b64url_decode(assertion["response"]["signature"]))
        raw[-1] ^= 0xFF
        assertion["response"]["signature"] = b64url(bytes(raw))
        return assertion


__all__ = [
    "FLAG_AT",
    "FLAG_BE",
    "FLAG_BS",
    "FLAG_UP",
    "FLAG_UV",
    "VirtualAuthenticator",
    "b64url",
    "b64url_decode",
]

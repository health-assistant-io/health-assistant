"""Secret-slot resolution for seed sources (ADR 0056 / ADR-0052 slots).

``seed_sources.auth_secret_ref`` names a secret SLOT — never a value. This
module is the health adapter over the family resolver
(:func:`nx_auth.secrets.get`, plan 60 D2): the kit owns storage, resolution
and redaction, health keeps the slot grammar, the service name, the env
prefix and the value caps.

* **OS keyring first** — ``health-assistant/seed-sources`` (the family
  secret-store law); the kit only trusts a keyring backend that round-trips
  a probe write, so a null/read-only/locked backend degrades instead of
  silently losing a credential;
* **server env fallback** — ``HA_SEED_SOURCE_SECRET_<SLOT>`` (the server
  product's sanctioned fallback per ADR-0052: "keyring/env in server
  products"), folded by the kit from the unprefixed ``HA_SEED_SOURCE_SECRET``
  prefix — a trailing underscore would produce a never-matching
  ``HA_SEED_SOURCE_SECRET__<SLOT>``;
* ``store_path=None`` — the sealed-file backend is study's; health has no
  plaintext-fallback store, only the keyring and the deployment env.

Missing slot / unavailable backend → ``None`` (the caller raises the typed
``SECRET_MISSING`` refusal — a fetch never proceeds unauthenticated by
accident). The helper never echoes a resolved secret; error rendering goes
through the kit's :func:`nx_auth.secrets.redact_secret` so a token that
leaked into an upstream message (e.g. git stderr echoing a URL) is masked
before the text reaches ``last_error`` or a log line.
"""

from __future__ import annotations

import re
from functools import lru_cache

from nx_auth import secrets as secret_store
from nx_auth.secrets import SecretCipher

KEYRING_SERVICE = "health-assistant/seed-sources"
"""The keyring service name every seed-source slot lives under."""

ENV_PREFIX = "HA_SEED_SOURCE_SECRET"
"""The kit env prefix (no trailing underscore — the kit adds the separator)."""

_MAX_SLOT_LEN = 256
_MAX_SECRET_LEN = 4096

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@lru_cache(maxsize=1)
def _sealer() -> SecretCipher | None:
    """The at-rest ring a sealed slot would be opened with, or ``None``.

    Health never *writes* a keyring slot (deployments inject them), so the
    cipher only ever decrypts an ``enc::`` envelope — and a host without
    ``HA_DATA_KEY`` still resolves its plaintext slots. A missing key must
    therefore not turn resolution into a 500: it degrades to "no sealer",
    and an unopenable envelope stays fail-closed (``None``).
    """
    from app.extensions.sdk.secrets import SecretCipher as SettingsSecretCipher

    try:
        return SettingsSecretCipher.from_settings()
    except (RuntimeError, ValueError):
        return None


def normalize_slot(ref: str) -> str:
    """The canonical slot name (trimmed, length-capped)."""
    return ref.strip()[:_MAX_SLOT_LEN]


def env_var_for_slot(ref: str) -> str:
    """The server env fallback variable name for a slot.

    The deployed spelling of the kit's frozen ``f"{env_prefix}_{slot}"``
    fold (non-alphanumeric runs → ``_``, uppercased). Kept as a thin alias
    for tests and operators; resolution itself hands ``ENV_PREFIX`` to the
    kit, so the two cannot drift apart silently
    (``test_secret_env_alias_matches_the_kit`` pins it).
    """
    return f"{ENV_PREFIX}_{_NON_ALNUM.sub('_', normalize_slot(ref)).upper()}"


def resolve_secret_slot(ref: str | None) -> str | None:
    """Resolve a secret slot to its credential (keyring, then env).

    Returns ``None`` when the reference is empty, unknown, or no backend
    holds a value — the caller refuses the fetch with a typed error instead
    of silently proceeding without credentials. The value is truncated at
    :data:`_MAX_SECRET_LEN` after resolution (the kit applies no cap of its
    own) and is never logged.
    """
    if not ref or not ref.strip():
        return None
    value = secret_store.get(
        normalize_slot(ref),
        service=KEYRING_SERVICE,
        env_prefix=ENV_PREFIX,
        store_path=None,
        sealer=_sealer(),
    )
    if not value:
        return None
    return str(value)[:_MAX_SECRET_LEN]

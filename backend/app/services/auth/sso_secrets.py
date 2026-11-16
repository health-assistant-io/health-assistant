"""Secret-slot resolution for tenant SSO client secrets (ADR 0054 D2).

``tenant_sso_configs.client_secret_ref`` names a secret SLOT — never a
value. This module is the health adapter over the family resolver
(:func:`nx_auth.secrets.get` / :func:`nx_auth.secrets.get_async`, plan 60
D2); the kit owns storage, resolution and redaction, health keeps the slot
grammar, the service name, the env prefix and the value caps.

* **OS keyring first** — ``health-assistant/tenant-sso`` (the family
  secret-store law); the kit only trusts a keyring backend that round-trips
  a probe write, so a null/read-only/locked backend degrades instead of
  silently losing a credential;
* **server env fallback** — ``HA_SSO_CLIENT_SECRET_<SLOT>`` (the server
  product's sanctioned fallback per ADR-0052), folded by the kit from the
  unprefixed ``HA_SSO_CLIENT_SECRET`` prefix — a trailing underscore would
  produce a never-matching ``HA_SSO_CLIENT_SECRET__<SLOT>``;
* ``store_path=None`` — the sealed-file backend is study's; health has no
  plaintext-fallback store, only the keyring and the deployment env.

Missing slot / unavailable backend → ``None`` (the caller raises the typed
``sso_client_secret_missing`` refusal — the flow never proceeds without
credentials by accident). The helper never echoes a resolved secret; error
rendering goes through the kit's
:func:`nx_auth.secrets.redact_secret`, which masks any secret an upstream
message echoed back.
"""

from __future__ import annotations

import re
from functools import lru_cache

from nx_auth import secrets as secret_store
from nx_auth.secrets import SecretCipher

KEYRING_SERVICE = "health-assistant/tenant-sso"
"""The keyring service name every tenant-SSO slot lives under."""

ENV_PREFIX = "HA_SSO_CLIENT_SECRET"
"""The kit env prefix (no trailing underscore — the kit adds the separator)."""

_MAX_SLOT_LEN = 256
_MAX_SECRET_LEN = 4096

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

#: A slot is a *name*: alphanumeric start, then letters/digits and the
#: separators the env fallback folds (``.``/``_``/``-``/``/``). Anything else
#: — spaces, control characters, punctuation a secret blob would carry — is
#: refused, so a pasted value can never masquerade as a slot reference.
_SLOT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


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


def is_valid_slot(ref: str | None) -> bool:
    """Whether ``ref`` is a well-formed secret-slot name (never a value)."""
    if not ref:
        return False
    slot = normalize_slot(ref)
    return bool(slot) and len(slot) <= _MAX_SLOT_LEN and bool(_SLOT_PATTERN.match(slot))


def env_var_for_slot(ref: str) -> str:
    """The server env fallback variable name for a slot."""
    return f"{ENV_PREFIX}_{_NON_ALNUM.sub('_', normalize_slot(ref)).upper()}"


def resolve_secret_slot(ref: str | None) -> str | None:
    """Resolve a secret slot to the OIDC client secret (keyring, then env).

    Returns ``None`` when the reference is empty, unknown, or no backend
    holds a value — the caller refuses the flow with a typed error instead
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


async def resolve_secret_slot_async(ref: str | None) -> str | None:
    """Awaitable :func:`resolve_secret_slot` for the async call sites.

    ``keyring`` is a blocking backend (Secret Service / DBus / a file store),
    so every request-path resolution runs in a worker thread instead of
    stalling the event loop — :func:`nx_auth.secrets.get_async` is the kit's
    own thread adapter. Same fail-closed contract: ``None`` means the caller
    raises its typed refusal.
    """
    if not ref or not ref.strip():
        return None
    value = await secret_store.get_async(
        normalize_slot(ref),
        service=KEYRING_SERVICE,
        env_prefix=ENV_PREFIX,
        store_path=None,
        sealer=_sealer(),
    )
    if not value:
        return None
    return str(value)[:_MAX_SECRET_LEN]

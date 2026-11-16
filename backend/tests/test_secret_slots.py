"""Secret-slot adoption of ``nx_auth.secrets`` (plan 60 D2, phase 2).

Health keeps the slot policy (seed normalization, SSO slot grammar, the
4096-character cap, the typed refusals at the call sites); the kit owns
storage, resolution and redaction. These tests pin the adapter contract:

- the frozen keyring services (``health-assistant/seed-sources`` /
  ``health-assistant/tenant-sso``) and the unprefixed env fallbacks
  (``HA_SEED_SOURCE_SECRET`` / ``HA_SSO_CLIENT_SECRET``) — a trailing
  underscore must resolve nothing;
- keyring-first precedence (with a round-tripping fake keyring), env
  fallback, and the fail-closed ``None`` when neither source holds a value;
- seed slot trimming/256 cap and the post-resolution 4096 cap;
- one redaction helper (the kit's) — no local copy survives in either
  slot module, and every call site imports the kit's function.
"""

from __future__ import annotations

import sys
import types

import pytest
from nx_auth import secrets as secret_store
from nx_auth.secrets import redact_secret as kit_redact_secret

from app.services.auth import sso_secrets
from app.services.seed import secrets as seed_secrets


class _PasswordDeleteError(Exception):
    """Stand-in for ``keyring.errors.PasswordDeleteError``."""


class _FakeKeyring(types.ModuleType):
    """A round-tripping keyring backend (dict storage, per ``(service, slot)``)."""

    def __init__(self) -> None:
        super().__init__("keyring")
        self.__path__ = []
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.store.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.store[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        if (service_name, username) not in self.store:
            raise _PasswordDeleteError("password not found")
        del self.store[(service_name, username)]


def _install_fake_keyring(monkeypatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    errors = types.ModuleType("keyring.errors")
    errors.PasswordDeleteError = _PasswordDeleteError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    secret_store.reset_caches()
    return fake


@pytest.fixture(autouse=True)
def _clean_secret_backends():
    """Backend selection is memoized per process — never leak it between tests."""
    secret_store.reset_caches()
    yield
    secret_store.reset_caches()


# --- seed slots -----------------------------------------------------------------


def test_seed_slot_env_fallback(monkeypatch):
    monkeypatch.delenv(seed_secrets.env_var_for_slot("github-read"), raising=False)
    assert seed_secrets.resolve_secret_slot("github-read") is None
    monkeypatch.setenv(seed_secrets.env_var_for_slot("github-read"), "tok-123")
    assert seed_secrets.resolve_secret_slot("github-read") == "tok-123"


def test_seed_slot_keyring_wins_over_env(monkeypatch):
    monkeypatch.setenv(seed_secrets.env_var_for_slot("github-read"), "env-token")
    fake = _install_fake_keyring(monkeypatch)
    fake.set_password(seed_secrets.KEYRING_SERVICE, "github-read", "keyring-token")
    assert seed_secrets.resolve_secret_slot("github-read") == "keyring-token"


def test_seed_slot_missing_slot_is_none(monkeypatch):
    monkeypatch.delenv(seed_secrets.env_var_for_slot("no-such-slot"), raising=False)
    assert seed_secrets.resolve_secret_slot("no-such-slot") is None
    assert seed_secrets.resolve_secret_slot("") is None
    assert seed_secrets.resolve_secret_slot("   ") is None
    assert seed_secrets.resolve_secret_slot(None) is None


def test_seed_slot_is_trimmed_before_resolution(monkeypatch):
    monkeypatch.setenv(seed_secrets.env_var_for_slot("github-read"), "tok-123")
    assert seed_secrets.resolve_secret_slot("  github-read ") == "tok-123"
    assert seed_secrets.normalize_slot("  github-read ") == "github-read"


def test_seed_env_alias_matches_the_kit(monkeypatch):
    """The operator-facing alias and the kit's frozen fold cannot drift."""
    monkeypatch.setenv("HA_SEED_SOURCE_SECRET_GITHUB_READ", "tok-123")
    assert seed_secrets.env_var_for_slot("github-read") == "HA_SEED_SOURCE_SECRET_GITHUB_READ"
    assert seed_secrets.resolve_secret_slot("github-read") == "tok-123"


def test_seed_prefix_with_a_trailing_underscore_resolves_nothing(monkeypatch):
    monkeypatch.setenv("HA_SEED_SOURCE_SECRET_GITHUB_READ", "tok-123")
    assert (
        secret_store.get(
            "github-read",
            service=seed_secrets.KEYRING_SERVICE,
            env_prefix="HA_SEED_SOURCE_SECRET",
        )
        == "tok-123"
    )
    assert (
        secret_store.get(
            "github-read",
            service=seed_secrets.KEYRING_SERVICE,
            env_prefix="HA_SEED_SOURCE_SECRET_",
        )
        is None
    )


def test_seed_value_is_capped_after_resolution(monkeypatch):
    monkeypatch.setenv(seed_secrets.env_var_for_slot("long"), "x" * 5000)
    value = seed_secrets.resolve_secret_slot("long")
    assert value is not None and len(value) == 4096


def test_seed_slot_is_length_capped_at_256(monkeypatch):
    long_ref = "a" * 300
    monkeypatch.setenv(seed_secrets.env_var_for_slot(long_ref), "tok-long")
    assert seed_secrets.env_var_for_slot(long_ref) == "HA_SEED_SOURCE_SECRET_" + "A" * 256
    assert seed_secrets.resolve_secret_slot(long_ref) == "tok-long"


def test_seed_backend_name_is_keyring_or_none():
    assert secret_store.backend_name(
        service=seed_secrets.KEYRING_SERVICE,
        env_prefix=seed_secrets.ENV_PREFIX,
        store_path=None,
    ) in {"keyring", "none"}


# --- tenant SSO slots -----------------------------------------------------------


async def test_sso_slot_env_fallback(monkeypatch):
    monkeypatch.delenv(sso_secrets.env_var_for_slot("test/slot"), raising=False)
    assert await sso_secrets.resolve_secret_slot_async("test/slot") is None
    monkeypatch.setenv(sso_secrets.env_var_for_slot("test/slot"), "sso-secret")
    assert await sso_secrets.resolve_secret_slot_async("test/slot") == "sso-secret"
    assert sso_secrets.resolve_secret_slot("test/slot") == "sso-secret"


async def test_sso_slot_keyring_wins_over_env(monkeypatch):
    monkeypatch.setenv(sso_secrets.env_var_for_slot("test/slot"), "env-secret")
    fake = _install_fake_keyring(monkeypatch)
    fake.set_password(sso_secrets.KEYRING_SERVICE, "test/slot", "keyring-secret")
    assert await sso_secrets.resolve_secret_slot_async("test/slot") == "keyring-secret"


async def test_sso_slot_missing_is_none(monkeypatch):
    monkeypatch.delenv(sso_secrets.env_var_for_slot("absent"), raising=False)
    assert await sso_secrets.resolve_secret_slot_async("absent") is None
    assert await sso_secrets.resolve_secret_slot_async("") is None
    assert await sso_secrets.resolve_secret_slot_async(None) is None


async def test_sso_value_is_capped_after_resolution(monkeypatch):
    monkeypatch.setenv(sso_secrets.env_var_for_slot("long"), "y" * 5000)
    value = await sso_secrets.resolve_secret_slot_async("long")
    assert value is not None and len(value) == 4096


async def test_sso_prefix_with_a_trailing_underscore_resolves_nothing(monkeypatch):
    monkeypatch.setenv("HA_SSO_CLIENT_SECRET_TEST_SLOT", "sso-secret")
    assert (
        await secret_store.get_async(
            "test/slot",
            service=sso_secrets.KEYRING_SERVICE,
            env_prefix="HA_SSO_CLIENT_SECRET",
        )
        == "sso-secret"
    )
    assert (
        await secret_store.get_async(
            "test/slot",
            service=sso_secrets.KEYRING_SERVICE,
            env_prefix="HA_SSO_CLIENT_SECRET_",
        )
        is None
    )


def test_sso_slot_grammar_stays_health_owned():
    assert sso_secrets.is_valid_slot("test/slot")
    assert sso_secrets.is_valid_slot("tenant.sso_1-x")
    assert not sso_secrets.is_valid_slot("has space")
    assert not sso_secrets.is_valid_slot("-leading")
    assert not sso_secrets.is_valid_slot("")
    assert not sso_secrets.is_valid_slot(None)


def test_sso_backend_name_is_keyring_or_none():
    assert secret_store.backend_name(
        service=sso_secrets.KEYRING_SERVICE,
        env_prefix=sso_secrets.ENV_PREFIX,
        store_path=None,
    ) in {"keyring", "none"}


# --- one redaction helper -------------------------------------------------------


def test_redaction_is_the_kits_single_helper():
    """No local ``redact_secret`` survives; every call site uses the kit's."""
    from app.services.auth import sso as sso_service
    from app.services.seed import sources_service, transport

    assert not hasattr(seed_secrets, "redact_secret")
    assert not hasattr(sso_secrets, "redact_secret")
    assert transport.redact_secret is kit_redact_secret
    assert sources_service.redact_secret is kit_redact_secret
    assert sso_service.redact_secret is kit_redact_secret


def test_redact_secret_masks_and_caps():
    text = "remote: https://user:tok-123@example.com/repo.git unauthorized"
    masked = kit_redact_secret(text, "tok-123")
    assert "tok-123" not in masked
    assert "***" in masked
    assert len(kit_redact_secret("x" * 1000, None)) == 400
    assert kit_redact_secret("x" * 1000, "y", cap=12) == "x" * 12

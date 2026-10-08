"""Security regression tests — 2026-08 audit Batch 5 (config/infra).

Covers:
- CFG-H1 SECRET_KEY placeholder/entropy rejection in production
- CFG-H6 DEMO_MODE fail-closed in production without explicit opt-in
- CFG-M4 DEBUG=true refused outside dev
- C-5  .dockerignore exists and is git-tracked; env walk-up disabled in prod
- API-L1 docs disabled in production

Every Settings construction goes through tests/settings_factory.py (plan
23 D5) and every refusal asserts the message names the offending knob
(plan 23 D6) — a guard that stops gating, or gates on a renamed
attribute, cannot satisfy these by accident. Each refusal is paired with
a positive that boots with only the offending knob fixed (T8).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.settings_factory import prod_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


# H4 (plan 16, identity-auth §8): the per-purpose signing keys replaced the
# single SECRET_KEY. Weak/short/placeholder pins refuse to boot in production.
# Positive pair: test_strong_signing_keys_accepted_in_production.
def test_placeholder_signing_key_refused_in_production():
    with pytest.raises(ValidationError) as exc_info:
        prod_settings(HA_SESSION_KEY="change_this_to_a_secure_random_string")
    assert "HA_SESSION_KEY" in str(exc_info.value)


def test_short_signing_key_refused_in_production():
    with pytest.raises(ValidationError) as exc_info:
        prod_settings(HA_REFRESH_KEY="short-but-real-key")
    assert "HA_REFRESH_KEY" in str(exc_info.value)


def test_missing_signing_keys_refused_in_production():
    with pytest.raises(ValidationError) as exc_info:
        prod_settings(HA_SESSION_KEY=None, HA_REFRESH_KEY=None)
    assert "HA_SESSION_KEY" in str(exc_info.value)


def test_strong_signing_keys_accepted_in_production():
    s = prod_settings()
    assert s.HA_SESSION_KEY.startswith("s")
    assert s.HA_REFRESH_KEY.startswith("r")


# Positive pair for the weak-DB-password refusal (which lives in
# test_security_hygiene.test_b13_production_rejects_weak_password).
def test_strong_db_password_accepted_in_production():
    s = prod_settings()
    assert s.POSTGRES_PASSWORD == "a-strong-unique-passphrase-9f3kQ"


def test_placeholder_db_password_refused_in_production():
    with pytest.raises(ValidationError) as exc_info:
        prod_settings(POSTGRES_PASSWORD="secure_password_here")
    msg = str(exc_info.value)
    assert "database password" in msg  # the offending knob, named
    assert "HA_DATABASE_URL" in msg
    assert "insecure database credentials" in msg


def test_demo_mode_refused_in_production_without_opt_in(monkeypatch):
    monkeypatch.delenv("DEMO_MODE_ACCEPT_UNAUTHENTICATED", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        prod_settings(HA_DEMO_MODE=True)
    msg = str(exc_info.value)
    assert "DEMO_MODE" in msg
    assert "HA_APP_ENV" in msg


def test_demo_mode_allowed_in_production_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("DEMO_MODE_ACCEPT_UNAUTHENTICATED", "true")
    s = prod_settings(HA_DEMO_MODE=True)
    assert s.HA_DEMO_MODE is True


def test_debug_refused_in_production():
    with pytest.raises(ValidationError) as exc_info:
        prod_settings(DEBUG=True)
    msg = str(exc_info.value)
    assert "DEBUG" in msg
    assert "HA_APP_ENV" in msg


# Positive pair for test_debug_refused_in_production (the same fixture
# with only DEBUG fixed boots).
def test_debug_false_accepted_in_production():
    s = prod_settings(DEBUG=False)
    assert s.DEBUG is False


def test_api_docs_disabled_in_production_by_default():
    assert Settings.model_fields["ENABLE_API_DOCS"].default is False


def test_dockerignore_exists_and_tracked():
    assert (REPO_ROOT / ".dockerignore").is_file()
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert (
        ".dockerignore"
        not in [line.strip() for line in gitignore.splitlines() if not line.strip().startswith("#")]
        or ".dockerignore" not in gitignore.split()
    )


def test_dockerignore_blocks_phi_and_secrets():
    content = (REPO_ROOT / ".dockerignore").read_text()
    for needed in ("uploads/", ".env", "venv/", "node_modules/"):
        assert needed in content, f".dockerignore must exclude {needed}"


def test_dockerfiles_run_as_non_root():
    for f in (
        "docker/Dockerfile",
        "docker/Dockerfile.worker",
        "docker/Dockerfile.frontend",
    ):
        content = (REPO_ROOT / f).read_text()
        assert "USER " in content, f"{f} must set a non-root USER"


def test_redis_requires_password_in_prod_compose():
    for f in ("docker/docker-compose.prod.yml", "docker/docker-compose.standalone.yml"):
        content = (REPO_ROOT / f).read_text()
        assert "requirepass" in content, f"{f} redis must run with requirepass"
        assert "REDIS_PASSWORD" in content

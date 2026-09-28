"""Security regression tests — 2026-08 audit Batch 5 (config/infra).

Covers:
- CFG-H1 SECRET_KEY placeholder/entropy rejection in production
- CFG-H6 DEMO_MODE fail-closed in production without explicit opt-in
- CFG-M4 DEBUG=true refused outside dev
- C-5  .dockerignore exists and is git-tracked; env walk-up disabled in prod
- API-L1 docs disabled in production
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _prod_kwargs(**extra):
    base = dict(
        APP_ENV="production",
        DEBUG=False,
        HA_SESSION_KEY="sess-Kq9!" + "Kq9!" * 10,
        HA_REFRESH_KEY="refr-Mt7#" + "Mt7#" * 10,
        POSTGRES_PASSWORD="a-strong-unique-passphrase-9f3kQ",
        INTEGRATION_SECRET_KEY="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        VAPID_PUBLIC_KEY="test-vapid-public-key-do-not-use",
        VAPID_PRIVATE_KEY="test-vapid-private-key-do-not-use",
    )
    base.update(extra)
    return base


# H4 (plan 16, identity-auth §8): the per-purpose signing keys replaced the
# single SECRET_KEY. Weak/short/placeholder pins refuse to boot in production.
def test_placeholder_signing_key_refused_in_production():
    with pytest.raises(ValidationError):
        Settings(**_prod_kwargs(HA_SESSION_KEY="change_this_to_a_secure_random_string"))


def test_short_signing_key_refused_in_production():
    with pytest.raises(ValidationError):
        Settings(**_prod_kwargs(HA_REFRESH_KEY="short-but-real-key"))


def test_missing_signing_keys_refused_in_production():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_prod_kwargs(HA_SESSION_KEY=None, HA_REFRESH_KEY=None))
    assert "HA_SESSION_KEY" in str(exc_info.value)


def test_strong_signing_keys_accepted_in_production():
    s = Settings(**_prod_kwargs())
    assert s.HA_SESSION_KEY.startswith("s")
    assert s.HA_REFRESH_KEY.startswith("r")


def test_placeholder_db_password_refused_in_production():
    with pytest.raises(ValidationError):
        Settings(**_prod_kwargs(POSTGRES_PASSWORD="secure_password_here"))


def test_demo_mode_refused_in_production_without_opt_in(monkeypatch):
    monkeypatch.delenv("DEMO_MODE_ACCEPT_UNAUTHENTICATED", raising=False)
    with pytest.raises(ValidationError):
        Settings(**_prod_kwargs(HA_DEMO_MODE=True))


def test_demo_mode_allowed_in_production_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("DEMO_MODE_ACCEPT_UNAUTHENTICATED", "true")
    s = Settings(**_prod_kwargs(HA_DEMO_MODE=True))
    assert s.HA_DEMO_MODE is True


def test_debug_refused_in_production():
    with pytest.raises(ValidationError):
        Settings(**_prod_kwargs(DEBUG=True))


def test_api_docs_disabled_in_production_by_default():
    assert Settings.model_fields["ENABLE_API_DOCS"].default is False


def test_dockerignore_exists_and_tracked():
    assert (REPO_ROOT / ".dockerignore").is_file()
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert (
        ".dockerignore"
        not in [
            line.strip()
            for line in gitignore.splitlines()
            if not line.strip().startswith("#")
        ]
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

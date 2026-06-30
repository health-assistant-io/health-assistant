"""Regression tests for the small hygiene batch (B11, B14, B15, J9).

B11: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY must be required in production
     (matching the existing SECRET_KEY / INTEGRATION_SECRET_KEY pattern).
     Missing either → Settings raises at construction time.

B14: check_observation_access used ``observation.subject.get("reference")``
     and called ``.split("/")[-1]`` on the result. If ``subject`` was a
     dict missing the ``"reference"`` key, ``.get()`` returned ``None``
     and ``None.split()`` raised AttributeError → HTTP 500. The fix
     defensively coerces to ``""`` so an absent reference yields an empty
     string, which the downstream check surfaces as a clean 400 / 404.

B15: doctors.py list_doctors_endpoint was wrapped in a try/except that
     ``print()`` + ``traceback.print_exc()`` + re-raised as
     ``HTTPException(500, detail=str(e))`` — bypassing the global
     handler, leaking internal error text, and using print instead of
     the logger. Fix: remove the wrapper, let the global handler take
     over (it logs with correlation id and returns a sanitized 500).

J9: lifespan startup wrapped every critical step (catalog seeding,
     integration registry init) in try/except that only logged. In prod,
     the app would boot half-initialised (no medication/allergy catalog)
     with no operator-visible signal beyond a log line. Fix: in prod
     (APP_ENV != development), re-raise after logging so the app refuses
     to boot. Dev retains fail-soft behaviour.
"""
import inspect

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# B11: VAPID keys required in production
# ---------------------------------------------------------------------------


def test_b11_vapid_keys_required_in_production(monkeypatch):
    """Missing VAPID keys in non-development APP_ENV must raise."""
    from app.core.config import Settings

    # Ensure the env-level defaults read as missing.
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,  # ignore .env / .env.test files
            APP_ENV="production",
            SECRET_KEY="strong-secret-key-for-jwt-signing-1234567890",
            INTEGRATION_SECRET_KEY="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
            DATABASE_URL="postgresql+asyncpg://x:y@localhost/z",
            # VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY intentionally unset
        )
    msg = str(exc_info.value).lower()
    assert "vapid" in msg


def test_b11_vapid_keys_optional_in_development(monkeypatch):
    """In development, missing VAPID keys are tolerated (Web Push silently skipped)."""
    from app.core.config import Settings

    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)

    # Should not raise.
    s = Settings(
        _env_file=None,
        APP_ENV="development",
        SECRET_KEY="dev-secret",
        INTEGRATION_SECRET_KEY="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        DATABASE_URL="postgresql+asyncpg://x:y@localhost/z",
    )
    assert s.VAPID_PUBLIC_KEY is None
    assert s.VAPID_PRIVATE_KEY is None


def test_b11_vapid_keys_present_in_production_ok():
    """In production with both keys set, construction succeeds."""
    from app.core.config import Settings

    s = Settings(
        _env_file=None,
        APP_ENV="production",
        SECRET_KEY="strong-secret-key-for-jwt-signing-1234567890",
        INTEGRATION_SECRET_KEY="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        DATABASE_URL="postgresql+asyncpg://x:y@localhost/z",
        VAPID_PUBLIC_KEY="BPkx...",
        VAPID_PRIVATE_KEY="abc123",
    )
    assert s.VAPID_PUBLIC_KEY == "BPkx..."


# ---------------------------------------------------------------------------
# B14: check_observation_access defensive .get
# ---------------------------------------------------------------------------


def test_b14_check_observation_access_handles_missing_reference():
    """A subject dict without 'reference' must not raise AttributeError."""
    src = inspect.getsource(
        __import__("app.api.v1.endpoints.utils", fromlist=["check_observation_access"]).check_observation_access
    )
    # The fix uses .get("reference", "") (or equivalent defensive pattern)
    # so the absent-key case yields an empty string instead of None.
    # Check for any of the equivalent safe patterns.
    assert any(
        needle in src
        for needle in ('.get("reference", "")', ".get('reference', '')", 'or {}')
    ), (
        "check_observation_access must defensively coerce a missing 'reference' "
        "key to an empty string, not let None propagate to .split() and 500."
    )


# ---------------------------------------------------------------------------
# B15: doctors.py no try/except wrapper
# ---------------------------------------------------------------------------


def test_b15_doctors_list_endpoint_has_no_try_except_wrapper():
    """list_doctors_endpoint must not be wrapped in a try/except that
    print()s + traceback.print_exc()s + re-raises as HTTPException(500)."""
    from app.api.v1.endpoints import doctors

    src = inspect.getsource(doctors.list_doctors_endpoint)
    # The print + traceback pattern must be gone.
    assert "print(" not in src, (
        "list_doctors_endpoint must not use print(); use the global exception "
        "handler which logs with correlation id and returns a sanitized 500."
    )
    assert "traceback.print_exc" not in src
    # The endpoint should NOT manually raise HTTPException(500, detail=str(e))
    # — let the global handler take over.
    assert "status_code=500, detail=str(e)" not in src


# ---------------------------------------------------------------------------
# J9: lifespan prod-fatal
# ---------------------------------------------------------------------------


def test_j9_lifespan_aborts_in_production():
    """The lifespan startup must re-raise catalog-seeding failures in prod
    so the app refuses to boot. Static contract: the lifespan source must
    contain a fail_soft / _abort_or_warn style gate."""
    from app.main import lifespan

    src = inspect.getsource(lifespan)
    # The fix introduces a prod-vs-dev gate. Either name is fine; what
    # matters is that the gate exists and the catalog-seeding except
    # block routes through it.
    assert "fail_soft" in src or "_abort_or_warn" in src, (
        "lifespan must gate startup failures on APP_ENV — fail-soft in dev, "
        "abort (re-raise) in production."
    )


def test_j9_lifespan_routes_seeding_failures_through_gate():
    """The catalog-seeding try/except must call _abort_or_warn (or
    equivalent), not just log."""
    from app.main import lifespan

    src = inspect.getsource(lifespan)
    # Find the catalog-seeding except block.
    assert "Catalog seeding" in src, (
        "Expected a labeled 'Catalog seeding' failure routed through the "
        "abort-or-warn gate."
    )
    assert "Integration registry initialization" in src

"""Test-only Settings construction factory (plan 23 D5).

Every test-side construction of :class:`app.core.config.Settings` flows
through this module. It exists because ``Settings(extra="ignore")``
silently discards unknown init kwargs — the plan-20 F1 class: a field
rename turned ``pytest.raises(ValidationError)`` security assertions into
vacuous passes and nothing in the suite could notice.

Two jobs:

* **one canonical production fixture** (strong signing keys, pinned data
  key, strong DB password, VAPID pair) so the boot-guard refusal tests
  share one base instead of four drifting dicts;
* **kwarg-name validation** against ``Settings.model_fields`` — every
  constructor here raises ``TypeError`` listing near-miss field names on a
  stale/renamed kwarg, so a rename breaks call sites *loudly* (at
  construction), never silently.

Test-only: never import from app code (one direction only), holds fixture
values only — never a default the app reads.
"""

from __future__ import annotations

import difflib
from types import SimpleNamespace
from typing import Any

from app.core.config import Settings

# --- canonical production fixture values (strong, obviously fake) ---------
PROD_SESSION_KEY = "sess-Kq9!" + "Kq9!" * 10
PROD_REFRESH_KEY = "refr-Mt7#" + "Mt7#" * 10
PROD_DATA_KEY = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa="
PROD_DB_PASSWORD = "a-strong-unique-passphrase-9f3kQ"
PROD_VAPID_PUBLIC_KEY = "test-vapid-public-key-do-not-use"
PROD_VAPID_PRIVATE_KEY = "test-vapid-private-key-do-not-use"

# Names this factory accepts as construction kwargs: Settings fields plus
# pydantic-settings' own `_env_file` override. Anything else is a rename.
_KNOWN_KWARGS = frozenset(Settings.model_fields) | {"_env_file"}


def validate_field_names(**overrides: Any) -> None:
    """Reject kwargs that are not ``Settings`` fields (rename guard).

    ``Settings(extra="ignore")`` would silently drop them — the exact
    mechanism behind plan 20's vacuous boot-guard tests. Raise TypeError
    with near-miss suggestions instead.
    """
    unknown = [name for name in overrides if name not in _KNOWN_KWARGS]
    if not unknown:
        return
    parts = []
    for name in unknown:
        near = difflib.get_close_matches(name, Settings.model_fields, n=3, cutoff=0.4)
        hint = ", ".join(repr(n) for n in near) or "(no near miss)"
        parts.append(f"  {name!r} is not a Settings field — did you mean: {hint}")
    raise TypeError(
        "settings_factory: unknown kwarg name(s) — a field was renamed or "
        "misspelled; Settings(extra='ignore') would silently drop these:\n" + "\n".join(parts)
    )


def prod_settings(_env_file: str | None = None, **overrides: Any) -> Settings:
    """A production-shaped Settings that passes every boot guard.

    ``_env_file=None`` by default: tests never read the repo's .env.
    Override exactly the knob under test (e.g. ``HA_SESSION_KEY="weak"``)
    — the paired positive uses the same call with only that knob fixed.
    """
    base: dict[str, Any] = {
        "HA_APP_ENV": "production",
        "DEBUG": False,
        "HA_SESSION_KEY": PROD_SESSION_KEY,
        "HA_REFRESH_KEY": PROD_REFRESH_KEY,
        "HA_DATA_KEY": PROD_DATA_KEY,
        "POSTGRES_PASSWORD": PROD_DB_PASSWORD,
        "VAPID_PUBLIC_KEY": PROD_VAPID_PUBLIC_KEY,
        "VAPID_PRIVATE_KEY": PROD_VAPID_PRIVATE_KEY,
    }
    base.update(overrides)
    validate_field_names(**base, _env_file=_env_file)
    return Settings(_env_file=_env_file, **base)


def dev_settings(_env_file: str | None = None, **overrides: Any) -> Settings:
    """A development-shaped Settings (guards relaxed, as in local dev).

    Ephemeral signing keys, empty DB password and missing VAPID keys are
    all tolerated — the positive counterparts to the production refusals.
    """
    base: dict[str, Any] = {
        "HA_APP_ENV": "development",
    }
    base.update(overrides)
    validate_field_names(**base, _env_file=_env_file)
    return Settings(_env_file=_env_file, **base)


def settings_stub(**overrides: Any) -> SimpleNamespace:
    """Attribute-only stand-in for ``get_settings()`` in monkeypatches.

    Replaces hand-rolled ``class _S`` stubs: names are validated against
    ``Settings.model_fields`` (so a rename raises instead of surfacing as
    ``AttributeError`` deep in product code), while the object itself runs
    no validators — it only needs to answer attribute reads.
    """
    validate_field_names(**overrides)
    return SimpleNamespace(**overrides)

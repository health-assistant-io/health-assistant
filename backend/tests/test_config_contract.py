"""Config rename-guard meta-tests (plan 23 D7 / T9).

The plan-20 F1 bug class: `Settings(extra="ignore")` silently drops
unknown init kwargs, so a field rename degrades a
``pytest.raises(ValidationError)`` security assertion into a silent
pass-through. These meta-tests pin the invariants that make that class
loud:

(a) the factory's kwarg names ⊆ ``Settings.model_fields`` — a rename
    without a factory update fails here *and* at the factory call sites
    (TypeError with near-miss suggestions);
(b) every env var the suite writes (``monkeypatch.setenv`` /
    ``os.environ`` writes / env-dict patches) is actually **read** by the
    app — the mirror-image guard ("code renamed, tests still setting the
    old env name"). Reader = ``app.core.config`` source by default, with
    a declared table for the integration-SDK names read elsewhere and a
    declared table for the retired names set to prove they are inert;
(c) the boot-guard field names exist and are the names the validators
    reference;
(d) no test constructs ``Settings`` directly or mutates the singleton
    with a bare ``setattr`` — everything flows through
    ``tests/settings_factory.py`` / ``monkeypatch`` (D5/D8).

It is deliberately a **declared table**, not AST magic: readable, cheap,
and stable across refactors. Source-contains checks — renames break the
table loudly, and the tables are greppable when they need to move.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.core.config import Settings
from tests import settings_factory

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

# (a) the names the factory pins its canonical fixtures on
FACTORY_KWARGS = (
    "HA_APP_ENV",
    "DEBUG",
    "HA_SESSION_KEY",
    "HA_REFRESH_KEY",
    "HA_DATA_KEY",
    "POSTGRES_PASSWORD",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
)

# (b) env names read outside app.core.config (declared, greppable)
READ_ELSEWHERE = {
    "PROMPT_GUARD_BLOCK_HIGH": ("app/utils/prompt_guard.py",),
    "INTEGRATION_ALLOWED_HOSTS": ("app/ai/providers/service.py",),
    "INTEGRATION_BLOCK_PRIVATE_RANGES": ("app/ai/providers/service.py",),
}

# (b) env names set by tests specifically to prove they are IGNORED (the
# "assert old names are inert" class) — behaviorally pinned by
# test_key_separation.test_legacy_env_names_are_gone.
UNREAD_BY_DESIGN = (
    "INTEGRATION_SECRET_KEY",
    "INTEGRATION_SECRET_KEY_PREVIOUS",
)

# (b) names written through patch.dict("os.environ", {...}) blocks (the
# dict keys sit on their own lines, so the write scan below can't see them)
PATCH_DICT_ENV_NAMES = (
    "HA_APP_ENV",
    "SETUP_TOKEN_MODE",
    "SETUP_BOOTSTRAP_TOKEN",
    "SETUP_TOKEN_GRACE_MINUTES",
)

# (c) the boot-guard field names (identity-auth §8 / 2026-08 audit)
GUARD_FIELDS = (
    "HA_APP_ENV",
    "HA_SESSION_KEY",
    "HA_REFRESH_KEY",
    "HA_DATA_KEY",
    "POSTGRES_PASSWORD",
    "DEBUG",
    "HA_DEMO_MODE",
)


def _suite_py_files() -> list[Path]:
    return sorted(
        p
        for p in TESTS_DIR.glob("*.py")
        # test_config_contract.py is the checker (its pattern literals must
        # not scan) and settings_factory.py is the sanctioned construction
        # site — everything else must go through it.
        if p.name not in {Path(__file__).name, "settings_factory.py"}
    )


def _suite_env_writes() -> dict[str, list[str]]:
    """name -> [file:line] for every env var the suite writes."""
    write_re = re.compile(
        r"""(?:monkeypatch\.setenv\(|os\.environ\.setdefault\(|os\.environ\[)"""
        r"""\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']"""
    )
    found: dict[str, list[str]] = {}
    for path in _suite_py_files():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for m in write_re.finditer(line):
                found.setdefault(m.group(1), []).append(f"{path.name}:{lineno}")
    return found


# ---------------------------------------------------------------------------
# (a) factory names ⊆ model fields
# ---------------------------------------------------------------------------


def test_factory_kwarg_names_are_settings_fields():
    missing = [name for name in FACTORY_KWARGS if name not in Settings.model_fields]
    assert not missing, (
        f"settings_factory canonical names vanished from Settings.model_fields: {missing} "
        "— a field was renamed; update tests/settings_factory.py AND this table "
        "(the factory raises TypeError at every call site until you do)."
    )


def test_factory_base_names_match_this_table():
    """The declared table and the factory's canonical base must agree."""
    base_names = {
        "HA_APP_ENV",
        "DEBUG",
        "HA_SESSION_KEY",
        "HA_REFRESH_KEY",
        "HA_DATA_KEY",
        "POSTGRES_PASSWORD",
        "VAPID_PUBLIC_KEY",
        "VAPID_PRIVATE_KEY",
    }
    assert base_names == set(FACTORY_KWARGS)
    assert settings_factory._KNOWN_KWARGS >= set(FACTORY_KWARGS)


def test_factory_rejects_renamed_kwarg_loudly():
    """T9: a near-miss name raises TypeError naming the suspects — it must
    never reach Settings(extra="ignore") and be silently dropped."""
    with pytest.raises(TypeError) as exc:
        settings_factory.prod_settings(HA_SESSSION_KEY="oops")
    msg = str(exc.value)
    assert "HA_SESSSION_KEY" in msg
    assert "HA_SESSION_KEY" in msg  # near-miss suggestion
    with pytest.raises(TypeError):
        settings_factory.dev_settings(APP_ENV="production")  # the F1 name itself
    with pytest.raises(TypeError):
        settings_factory.settings_stub(SESSION_KEY="x")


# ---------------------------------------------------------------------------
# (b) env writes are read by the app (mirror-image rename guard)
# ---------------------------------------------------------------------------


def test_suite_env_writes_are_read_by_the_app():
    writes = _suite_env_writes()
    for name in sorted(set(writes) | set(PATCH_DICT_ENV_NAMES)):
        if name in UNREAD_BY_DESIGN:
            continue
        if name in READ_ELSEWHERE:
            readers = READ_ELSEWHERE[name]
        else:
            readers = ("app/core/config.py",)
        for reader in readers:
            src = (BACKEND_DIR / reader).read_text()
            assert name in src, (
                f"the suite writes env {name!r} ({writes.get(name, ['<patch.dict>'])}) "
                f"but {reader} never mentions it — the reader was renamed and the "
                "test now sets a dead env var (plan 20 F1's mirror image)."
            )


def test_unread_by_design_names_are_declared():
    """The inert-name table must stay a small, deliberate list."""
    assert all(name.isupper() for name in UNREAD_BY_DESIGN)
    assert set(UNREAD_BY_DESIGN).isdisjoint(READ_ELSEWHERE)


# ---------------------------------------------------------------------------
# (c) boot-guard field names pinned
# ---------------------------------------------------------------------------


def test_guard_field_names_exist_and_are_validator_names():
    settings_src = inspect.getsource(Settings)
    for name in GUARD_FIELDS:
        assert name in Settings.model_fields, (
            f"boot-guard field {name!r} vanished from Settings.model_fields — "
            "renamed? Update the guards' tests (they assert the message names "
            "this knob) and this table together."
        )
        assert name in settings_src, (
            f"boot-guard name {name!r} is no longer referenced anywhere in the "
            "Settings class body/validators — the guard may now gate on a "
            "renamed attribute (plan 20 F1's vacuous-assertion class)."
        )


# ---------------------------------------------------------------------------
# (d) construction/mutation discipline (D5/D8)
# ---------------------------------------------------------------------------


def test_all_settings_construction_flows_through_the_factory():
    construct_re = re.compile(r"\bSettings\(")
    offenders = []
    for path in _suite_py_files():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if construct_re.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "tests must construct Settings via tests/settings_factory.py "
        "(prod_settings / dev_settings / settings_stub) so kwarg names are "
        "validated against Settings.model_fields:\n" + "\n".join(offenders)
    )


def test_no_bare_singleton_setattr_in_tests():
    """D8: singleton mutation goes through monkeypatch (auto-restoration);
    a bare setattr leaks across tests under xdist."""
    bare_re = re.compile(r"^\s*setattr\(")
    offenders = []
    for path in _suite_py_files():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if bare_re.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "use monkeypatch.setattr(settings, ...):\n" + "\n".join(offenders)

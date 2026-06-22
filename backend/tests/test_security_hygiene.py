"""Tests for audit items B7, B10, B13, B14 (low-risk security hygiene).

B7:  two ``print()`` calls in ``ai_assistance_service`` dumped user input +
     LLM output to stdout. Replaced with ``logger.debug``.
B10: CORS production fallback used an underscore in the hostname
     (``app.health_assistant.com``) — invalid per RFC 1123, so the rule
     never matched a real origin.
B13: ``POSTGRES_PASSWORD`` default ``admin123`` shipped in code + .env.example.
     Default removed; production validator refuses weak credentials.
B14: ``catalog_search_service._set_similarity_threshold`` interpolated
     ``threshold`` into raw SQL via f-string. Now validates a finite float
     in [0, 1] before inlining.
"""
import importlib
import inspect

import pytest


# ---------------------------------------------------------------------------
# B7: no print() leaks in ai_assistance_service
# ---------------------------------------------------------------------------


def test_b7_no_print_calls_in_ai_assistance_service():
    """B7: the debug ``print()`` statements that leaked user input + LLM
    output to stdout must be gone from the two define_* methods."""
    import ast

    from app.services import ai_assistance_service

    source = inspect.getsource(ai_assistance_service)
    tree = ast.parse(source)

    print_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    assert print_calls == [], (
        f"ai_assistance_service.py still contains {len(print_calls)} top-level "
        "print() call(s) — debug stdout leaks."
    )


@pytest.mark.asyncio
async def test_b7_define_biomarker_uses_logger_not_print(monkeypatch, caplog):
    """B7: _define_biomarker must route through logger.debug, not print."""
    import logging
    from unittest.mock import AsyncMock, MagicMock

    from app.services.ai_assistance_service import AIAssistanceService

    svc = AIAssistanceService.__new__(AIAssistanceService)

    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"name": "Hemoglobin A1c"}

    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(return_value=fake_result)
    llm = MagicMock()
    llm.with_structured_output.return_value = structured_llm

    captured = {}

    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.setdefault("printed", a))

    with caplog.at_level(logging.DEBUG, logger="app.services.ai_assistance_service"):
        out = await svc._define_biomarker(llm, "Hemoglobin A1c", {})

    assert out["success"] is True
    assert "printed" not in captured, (
        "_define_biomarker called print() — must use logger"
    )


# ---------------------------------------------------------------------------
# B10: CORS production fallback hostname has no underscore
# ---------------------------------------------------------------------------


def test_b10_cors_default_has_no_underscore():
    """B10: the production CORS fallback origin must be a valid hostname.

    Underscores are illegal in DNS names (RFC 1123 §2.1) so the previous
    ``https://app.health_assistant.com`` could never match a real origin.
    """
    import re

    src = inspect.getsource(importlib.import_module("app.main"))
    # Extract the actual default string literal, ignoring comments / docstrings.
    match = re.search(r'allow_origins=\[os\.getenv\("FRONTEND_URL",\s*"([^"]+)"\)\]', src)
    assert match, "Could not locate CORS allow_origins fallback in main.py"
    fallback = match.group(1)
    assert "_" not in fallback.split("://", 1)[1], (
        f"CORS fallback {fallback!r} contains an underscore in the hostname "
        " — invalid per RFC 1123."
    )


def test_b10_cors_default_is_valid_hostname():
    """B10: sanity-check the fallback hostname parses cleanly."""
    import re


    src = inspect.getsource(importlib.import_module("app.main"))
    match = re.search(r'allow_origins=\[os\.getenv\("FRONTEND_URL",\s*"([^"]+)"\)\]', src)
    assert match, "Could not locate CORS allow_origins fallback in main.py"
    fallback = match.group(1)
    # Validate the host portion is RFC-1123 clean (letters, digits, hyphens, dots).
    host = fallback.split("://", 1)[1].split("/")[0]
    assert re.fullmatch(r"[a-z0-9.\-]+", host), (
        f"CORS fallback host {host!r} contains illegal characters."
    )
    assert "_" not in host


# ---------------------------------------------------------------------------
# B13: POSTGRES_PASSWORD has no insecure default + prod validator
# ---------------------------------------------------------------------------


def test_b13_password_default_is_empty():
    """B13: the code default for POSTGRES_PASSWORD must NOT be a known value."""
    from app.core.config import Settings

    field = Settings.model_fields["POSTGRES_PASSWORD"]
    assert field.default == "", (
        f"POSTGRES_PASSWORD still ships a code default of {field.default!r} "
        " — must require explicit configuration."
    )


@pytest.mark.parametrize("weak", ["", "admin123", "password", "postgres", "secret", "changeme"])
def test_b13_production_rejects_weak_password(weak, monkeypatch):
    """B13: booting with a known-weak DB password outside development fails."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError) as exc_info:
        Settings(APP_ENV="production", POSTGRES_PASSWORD=weak)
    assert "POSTGRES_PASSWORD" in str(exc_info.value) or "insecure" in str(exc_info.value)


def test_b13_production_accepts_strong_password():
    """B13: a non-default strong password boots fine in production."""
    from app.core.config import Settings

    s = Settings(APP_ENV="production", POSTGRES_PASSWORD="a-strong-unique-passphrase-9f3kQ")
    assert s.POSTGRES_PASSWORD == "a-strong-unique-passphrase-9f3kQ"


def test_b13_development_allows_empty_for_local_dev():
    """B13: development/test still tolerates an unset password (local dev)."""
    from app.core.config import Settings

    s = Settings(APP_ENV="development", POSTGRES_PASSWORD="")
    assert s.POSTGRES_PASSWORD == ""


def test_b13_env_example_has_no_admin123():
    """B13: the .env.example template must not ship the legacy weak password."""
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    if not env_example.exists():
        pytest.skip(".env.example not present")
    content = env_example.read_text()
    assert "admin123" not in content, (
        ".env.example still references the legacy admin123 password."
    )


# ---------------------------------------------------------------------------
# B14: _set_similarity_threshold validates threshold before inlining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_value",
    [
        None,
        "not a number",
        "1; DROP TABLE users; --",
        1.5,        # out of range high
        -0.1,       # out of range low
        float("inf"),
        float("nan"),
    ],
)
async def test_b14_bad_threshold_falls_back_to_default(bad_value):
    """B14: any non-finite or out-of-[0,1] value falls back to the safe default."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.catalog_search_service import (
        DEFAULT_THRESHOLD,
        _set_similarity_threshold,
    )

    db = MagicMock()
    db.execute = AsyncMock()

    await _set_similarity_threshold(db, bad_value)

    assert db.execute.await_count == 1
    sql_arg = db.execute.await_args.args[0]
    rendered = str(sql_arg)
    assert str(DEFAULT_THRESHOLD) in rendered, (
        f"Threshold {bad_value!r} did not fall back to default {DEFAULT_THRESHOLD}"
    )
    # No injection vector can survive — rendered text is only the safe float.
    assert "DROP" not in rendered.upper()


@pytest.mark.asyncio
@pytest.mark.parametrize("good_value", [0.1, 0.2, 0.5, 0.9, 1.0, 0.0])
async def test_b14_valid_threshold_is_inlined(good_value):
    """B14: a valid float in [0, 1] is passed through to the SET statement."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.catalog_search_service import _set_similarity_threshold

    db = MagicMock()
    db.execute = AsyncMock()

    await _set_similarity_threshold(db, good_value)

    rendered = str(db.execute.await_args.args[0])
    assert str(good_value) in rendered


@pytest.mark.asyncio
async def test_b14_threshold_uses_set_statement():
    """B14: the function still emits a pg_trgm similarity_threshold SET."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.catalog_search_service import _set_similarity_threshold

    db = MagicMock()
    db.execute = AsyncMock()
    await _set_similarity_threshold(db, 0.3)
    rendered = str(db.execute.await_args.args[0])
    assert "pg_trgm.similarity_threshold" in rendered

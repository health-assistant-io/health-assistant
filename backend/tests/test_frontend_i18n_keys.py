"""Server → frontend i18n key contract.

The frontend catalogue is the only home for user-visible copy, but the backend
still *emits keys* for copy it causes to render: catalog labels, settings
definitions and enum options, setup-wizard step titles and descriptions, and
slug-keyed event copy.

A key emitted here that the catalogue lacks renders as the raw key in the UI
(e.g. the language dropdown showed ``settings.language_en``), and neither wall
that guards the catalogue can see it: ``check_translations.py`` compares
dictionaries with each other and ``i18n.audit.test.ts`` scans ``t()`` calls in
TS/TSX. This module is the third wall.

No DB, no fixtures — it reads the shipped locale files and the seed JSON
directly, mirroring ``test_ai_task_registry.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.catalogs import CatalogRegistry
from app.core.settings_definitions import SETTINGS_CATEGORIES, SETTINGS_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "src" / "locales"
BACKEND = Path(__file__).resolve().parents[1]
SETUP_SERVICE = BACKEND / "app" / "services" / "setup" / "setup_checklist_service.py"
SEEDS = BACKEND / "data" / "seeds"

SHIPPED = sorted(p.stem for p in LOCALES.glob("*.json"))


def _locale_keys(locale: str) -> set[str]:
    data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    keys: set[str] = set()

    def walk(prefix: str, node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else key, value)
        else:
            keys.add(prefix)

    walk("", data)
    return keys


@pytest.fixture(scope="module")
def catalogues() -> dict[str, set[str]]:
    assert SHIPPED >= ["el", "en"], f"both shipped locales must exist, found {SHIPPED}"
    return {locale: _locale_keys(locale) for locale in SHIPPED}


@pytest.mark.parametrize("locale", SHIPPED)
def test_catalog_label_keys_resolve(catalogues: dict[str, set[str]], locale: str) -> None:
    missing = [
        f"{catalog.type}: {catalog.ui.label_key}"
        for catalog in CatalogRegistry.all()
        if catalog.ui.label_key not in catalogues[locale]
    ]
    assert missing == [], f"{locale}: catalog labels missing from the catalogue: {missing}"


@pytest.mark.parametrize("locale", SHIPPED)
def test_settings_keys_resolve(catalogues: dict[str, set[str]], locale: str) -> None:
    keys = catalogues[locale]
    missing: list[str] = []

    for category in SETTINGS_CATEGORIES:
        if category.label_key not in keys:
            missing.append(f"category {category.key}: {category.label_key}")
        if category.description_key and category.description_key not in keys:
            missing.append(f"category {category.key}: {category.description_key}")

    for definition in SETTINGS_REGISTRY:
        if definition.label_key not in keys:
            missing.append(f"{definition.key}: {definition.label_key}")
        if definition.description_key and definition.description_key not in keys:
            missing.append(f"{definition.key}: {definition.description_key}")
        for option in definition.options or []:
            # Reference-catalog options (languages) carry no key by design —
            # the app resolves them through languageDisplayName (ADR-0024).
            if option.label_key and option.label_key not in keys:
                missing.append(f"{definition.key}/{option.value}: {option.label_key}")

    assert missing == [], f"{locale}: settings keys missing from the catalogue: {missing}"


def _setup_step_ids() -> set[str]:
    source = SETUP_SERVICE.read_text(encoding="utf-8")
    titles = set(re.findall(r'"(setup\.steps\.[a-z0-9_.]+)"', source))
    return {title.removeprefix("setup.steps.") for title in titles}


@pytest.mark.parametrize("locale", SHIPPED)
def test_setup_step_titles_resolve(catalogues: dict[str, set[str]], locale: str) -> None:
    missing = sorted(
        f"setup.steps.{step_id}"
        for step_id in _setup_step_ids()
        if f"setup.steps.{step_id}" not in catalogues[locale]
    )
    assert missing == [], f"{locale}: setup step titles missing: {missing}"


@pytest.mark.parametrize("locale", SHIPPED)
def test_setup_step_descriptions_match_the_steps(
    catalogues: dict[str, set[str]], locale: str
) -> None:
    prefix = "setup.step_desc."
    descriptions = {
        key.removeprefix(prefix)
        for key in catalogues[locale]
        if key.startswith(prefix)
    }
    # ``error`` is a shared message key, not a wizard step.
    expected = {step_id.replace(".", "__") for step_id in _setup_step_ids() if step_id != "error"}
    assert descriptions - expected == set(), (
        f"{locale}: step_desc keys with no matching step: {sorted(descriptions - expected)}"
    )
    assert expected - descriptions == set(), (
        f"{locale}: steps without a description key: {sorted(expected - descriptions)}"
    )


@pytest.mark.parametrize("locale", SHIPPED)
def test_event_seed_copy_resolves(catalogues: dict[str, set[str]], locale: str) -> None:
    """Seeded event types/categories are translated by slug (docs/CLINICAL_EVENTS.md)."""
    keys = catalogues[locale]
    types_path = SEEDS / "clinical_event_types.json"
    type_items = json.loads(types_path.read_text(encoding="utf-8"))["items"]
    concepts = json.loads((SEEDS / "concepts.json").read_text(encoding="utf-8"))["items"]
    category_items = [item for item in concepts if "event_category" in (item.get("kinds") or [])]

    missing: list[str] = []
    for item in type_items:
        for field in ("name", "description"):
            key = f"events.type.{item['slug']}.{field}"
            if key not in keys:
                missing.append(key)
    for item in category_items:
        for field in ("name", "description"):
            key = f"events.category.{item['slug']}.{field}"
            if key not in keys:
                missing.append(key)

    assert missing == [], f"{locale}: seeded event copy missing: {missing}"

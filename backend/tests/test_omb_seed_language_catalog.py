"""Membership test: every OMB seed language exists in the shared language
catalog (family ADR-0024 §3.4 synced-data channel).

The seed's ``languages`` slice is the curated, server-driven picklist for
patient ``preferred_language`` (GET /setup/extension-catalog); the catalog
at ``data/catalogs/languages.json`` is the family-wide authority. This
guards against the curated slice drifting to codes the catalog dropped.
"""

import json
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SEED_PATH = _BACKEND_DIR / "data" / "seeds" / "omb_race_ethnicity.json"
_CATALOG_PATH = _BACKEND_DIR / "data" / "catalogs" / "languages.json"


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_omb_seed_language_codes_exist_in_shared_catalog():
    """Every ``{code, display}`` seed entry resolves in the shared catalog."""
    seed_codes = {entry["code"] for entry in _load_json(_SEED_PATH)["languages"]}
    catalog_codes = {entry["code"] for entry in _load_json(_CATALOG_PATH)["languages"]}

    assert seed_codes, "OMB seed 'languages' slice is empty"
    missing = sorted(seed_codes - catalog_codes)
    assert not missing, f"seed language codes missing from data/catalogs/languages.json: {missing}"

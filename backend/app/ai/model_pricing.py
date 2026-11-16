"""Pure reference resolver for the family model-pricing contract.

Stdlib only, no I/O. Rows and the compiled date are passed in, so the same
bytes can be vendored at ``contracts/model_pricing.py`` and
``backend/app/ai/model_pricing.py`` without an import rewrite. Desktop has
the TS twin (``src/shared/modelPricing.ts``); both are pinned by
``contracts/model-pricing.fixtures.json``.

Truth table: explicit user/override row -> loopback/mDNS = 0 -> built-in ->
unknown. Within a class: exact/alias > longest delimiter-aware prefix; then
``resolved_model`` before the requested ``model``. A built-in dated match
never outranks a user row on the requested base id.

An override may bind a preset key (``provider``) or a concrete product
provider row (``providerRef``); the latter is the only honest binding for a
custom provider with no preset key. Built-ins never match a ``providerRef``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DELIMITERS = frozenset("-./:@_")
MAX_SAFE_INTEGER = 9_007_199_254_740_991
METERING = frozenset({"tokens", "input_only", "output_only"})


@dataclass(frozen=True)
class PriceResolution:
    source: str
    row: Mapping[str, Any] | None
    input_micros_per_million: int
    output_micros_per_million: int
    metering: str
    cost_micros: int | None
    price_version: str | None
    reviewed_at: str | None
    matched_model: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "row": dict(self.row) if self.row is not None else None,
            "inputMicrosPerMillion": self.input_micros_per_million,
            "outputMicrosPerMillion": self.output_micros_per_million,
            "metering": self.metering,
            "costMicros": self.cost_micros,
            "priceVersion": self.price_version,
            "reviewedAt": self.reviewed_at,
            "matchedModel": self.matched_model,
        }


def _trim(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _prefix_matches(candidate: str, prefix: str) -> bool:
    if not prefix or not candidate.startswith(prefix):
        return False
    if len(candidate) == len(prefix):
        return True
    return candidate[len(prefix)] in DELIMITERS or prefix[-1] in DELIMITERS


def match_id(row: Mapping[str, Any], candidate: str) -> tuple[int, int] | None:
    """Return (specificity, length): 2 exact/alias, 1 prefix."""
    match = row.get("match")
    if isinstance(match, str):
        if match == "prefix":
            return (1, len(row["model"])) if _prefix_matches(candidate, row["model"]) else None
        return (2, len(row["model"])) if candidate == row["model"] else None
    if not isinstance(match, Mapping):
        return None
    best: tuple[int, int] | None = None
    for value in list(match.get("exact", [])) + list(match.get("aliases", [])):
        if candidate == value:
            best = (2, len(value))
    for prefix in match.get("prefix", []):
        if _prefix_matches(candidate, prefix):
            current = (1, len(prefix))
            if best is None or current > best:
                best = current
    return best


def _user_match(
    row: Mapping[str, Any],
    candidate: str,
    provider_key: str | None,
    provider_row_id: str | None,
    is_local: bool,
) -> tuple[int, int, int] | None:
    provider_ref = row.get("providerRef")
    provider = row.get("provider")
    if provider_ref is not None:
        if provider_row_id is None or provider_ref != provider_row_id:
            return None
        specificity = 2
    elif provider is not None:
        if provider_key is None or provider != provider_key:
            return None
        specificity = 1
    else:
        if is_local:
            return None
        specificity = 0
    matched = match_id(row, candidate)
    if matched is None:
        return None
    return (matched[0], matched[1], specificity)


def _metering(row: Mapping[str, Any]) -> str:
    value = row.get("metering", "tokens")
    return value if value in METERING else "tokens"


def cost_micros(
    row: Mapping[str, Any],
    input_tokens: int | None,
    output_tokens: int | None,
) -> int | None:
    metering = _metering(row)
    prompt = input_tokens
    completion = output_tokens
    if metering == "input_only":
        completion = 0
    elif metering == "output_only":
        prompt = 0
    if prompt is None or completion is None or prompt < 0 or completion < 0:
        return None
    numerator = (
        prompt * int(row.get("inputMicrosPerMillion", 0))
        + completion * int(row.get("outputMicrosPerMillion", 0))
    )
    if numerator < 0 or numerator > MAX_SAFE_INTEGER - 500_000:
        return None
    return (numerator + 500_000) // 1_000_000


def _canonical(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def price_version(
    row: Mapping[str, Any],
    source: str,
    compiled: str,
) -> str:
    if source == "user":
        identifier = row.get("id")
        updated = row.get("updatedAt")
        if identifier is not None or updated is not None:
            return f"override:{identifier or ''}:{updated or ''}"
    digest = hashlib.sha256(_canonical(row).encode("utf-8")).hexdigest()[:8]
    return f"model-pricing/1:{compiled}:{digest}"


def resolve_price(
    *,
    rows: Sequence[Mapping[str, Any]],
    compiled: str,
    model: str,
    provider_key: str | None = None,
    provider_row_id: str | None = None,
    resolved_model: str | None = None,
    is_local: bool = False,
    user_rows: Sequence[Mapping[str, Any]] = (),
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    include_retired: bool = False,
) -> PriceResolution | None:
    requested = _trim(model)
    resolved = _trim(resolved_model)
    if requested is None:
        return None
    candidates = [candidate for candidate in (resolved, requested) if candidate]

    best_user: tuple[tuple[int, int, int, int], Mapping[str, Any], str] | None = None
    for index, candidate in enumerate(candidates):
        for row in user_rows:
            matched = _user_match(row, candidate, provider_key, provider_row_id, is_local)
            if matched is None:
                continue
            rank = (matched[0], matched[1], matched[2], -index)
            if best_user is None or rank > best_user[0]:
                best_user = (rank, row, candidate)
    if best_user is not None:
        row = best_user[1]
        return PriceResolution(
            source="user",
            row=row,
            input_micros_per_million=int(row.get("inputMicrosPerMillion", 0)),
            output_micros_per_million=int(row.get("outputMicrosPerMillion", 0)),
            metering=_metering(row),
            cost_micros=cost_micros(row, input_tokens, output_tokens),
            price_version=price_version(row, "user", compiled),
            reviewed_at=None,
            matched_model=best_user[2],
        )

    if is_local:
        return PriceResolution(
            source="local",
            row=None,
            input_micros_per_million=0,
            output_micros_per_million=0,
            metering="tokens",
            cost_micros=0,
            price_version=None,
            reviewed_at=None,
            matched_model=None,
        )

    best_builtin: tuple[tuple[int, int, int], Mapping[str, Any], str] | None = None
    if provider_key is not None:
        for index, candidate in enumerate(candidates):
            for row in rows:
                if row.get("provider") != provider_key:
                    continue
                if row.get("retiredAt") and not include_retired:
                    continue
                builtin_matched = match_id(row, candidate)
                if builtin_matched is None:
                    continue
                builtin_rank = (builtin_matched[0], builtin_matched[1], -index)
                if best_builtin is None or builtin_rank > best_builtin[0]:
                    best_builtin = (builtin_rank, row, candidate)
    if best_builtin is None:
        return None
    row = best_builtin[1]
    return PriceResolution(
        source="builtin",
        row=row,
        input_micros_per_million=int(row.get("inputMicrosPerMillion", 0)),
        output_micros_per_million=int(row.get("outputMicrosPerMillion", 0)),
        metering=_metering(row),
        cost_micros=cost_micros(row, input_tokens, output_tokens),
        price_version=price_version(row, "builtin", compiled),
        reviewed_at=row.get("reviewed"),
        matched_model=best_builtin[2],
    )

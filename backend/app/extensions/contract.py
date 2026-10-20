"""contract — the extension manifest envelope: constants, secret-field
helpers and the one draft-2020-12 validator (ADR-0040 clause 5 / D11).

Vendored byte-identical from ``dev/contracts/contract.py`` (ADR-0031
sync-and-gate; never edited in-repo). Stdlib-only so any host (and the
dev contract walls) can import it without dependencies.

The manifest's single source of truth is the schema artifact
(``extension-manifest.schema.json``) plus the charter vocabularies
(``extension-charter.json``): this module validates a manifest document
AGAINST a schema loaded by the caller (hosts load their vendored schema
copy) — the validator is generic over the supported draft-2020-12
keyword subset, the same subset ``scripts/check-extension-contract.py``
pins at the dev gate. A manifest passing structure is NOT "valid":
host-owned rules beyond structure (tier x kind x capability legality,
cross-references, reserved id prefixes, byte caps, apiVersion ceilings)
are the host validator's job — see the schema's ``$comment``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "ENVELOPE_API_VERSION",
    "KINDS",
    "CONNECTOR_KIND_VERSION",
    "DOCS_POINTER",
    "RESERVED_ID_PREFIXES",
    "docs_tree_semantics",
    "is_timeout_safe_pattern",
    "manifest_i18n_semantics",
    "run_manifest_fixtures",
    "secret_fields",
    "validate",
    "validate_manifest",
    "validate_registration",
]

ENVELOPE_API_VERSION = "extension/v1"
KINDS = ("connector", "mini-app", "bridge")
CONNECTOR_KIND_VERSION = 1
RESERVED_ID_PREFIXES = ("host-", "neuro-")
#: plan 34 §10.3 — the manifest's optional per-extension docs pointer;
#: the one fixed value the envelope schema admits.
DOCS_POINTER = "docs/docs-tree.json"

_SUPPORTED_KEYWORDS = frozenset(
    {
        "type", "enum", "const", "properties", "propertyNames", "additionalProperties",
        "required", "items", "minItems", "maxItems", "uniqueItems", "contains",
        "minLength", "maxLength", "pattern", "minimum", "maximum",
        "maxProperties", "minProperties", "allOf", "anyOf", "oneOf", "not",
        "if", "then", "else", "$ref",
    }
)


def _resolve_ref(root: Any, ref: str) -> Any:
    if ref == "#":
        return root
    if ref.startswith("#/"):
        node = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"unresolvable $ref {ref}")
            node = node[part]
        return node
    raise KeyError(f"unsupported $ref {ref} (only '#' and '#/$defs/...' are supported)")


def _type_ok(name: str, inst: Any) -> bool:
    if name == "object":
        return isinstance(inst, dict)
    if name == "array":
        return isinstance(inst, list)
    if name == "string":
        return isinstance(inst, str)
    if name == "boolean":
        return isinstance(inst, bool)
    if name == "integer":
        return isinstance(inst, int) and not isinstance(inst, bool)
    if name == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if name == "null":
        return inst is None
    raise KeyError(f"unknown type {name}")


def validate(schema: Any, inst: Any, root: Any | None = None, path: str = "$") -> str | None:
    """Return None when ``inst`` validates, else a path-precise error.

    Supports the closed draft-2020-12 keyword subset the family schemas
    use (checked at the dev gate); python ``re`` stands in for ECMA
    regexps, adequate for the patterns authored here.
    """
    if root is None:
        root = schema
    if schema is True:
        return None
    if schema is False:
        return f"{path}: schema forbids any value here"
    if not isinstance(schema, dict):
        return f"{path}: malformed schema node ({type(schema).__name__})"

    if "$ref" in schema:
        target = _resolve_ref(root, schema["$ref"])
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        if rest:
            err = validate(target, inst, root, path)
            if err:
                return err
            return validate(rest, inst, root, path)
        return validate(target, inst, root, path)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(t, inst) for t in types):
            return f"{path}: expected type {'|'.join(types)}, got {type(inst).__name__}"

    if "enum" in schema and inst not in schema["enum"]:
        if isinstance(inst, bool) or not any(isinstance(v, bool) for v in schema["enum"]):
            if inst not in schema["enum"]:
                return f"{path}: {inst!r} not in enum {schema['enum']!r}"
    if "const" in schema and inst != schema["const"]:
        return f"{path}: expected const {schema['const']!r}, got {inst!r}"

    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            return f"{path}: shorter than minLength {schema['minLength']}"
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            return f"{path}: longer than maxLength {schema['maxLength']}"
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            return f"{path}: {inst!r} does not match pattern {schema['pattern']!r}"
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            return f"{path}: below minimum {schema['minimum']}"
        if "maximum" in schema and inst > schema["maximum"]:
            return f"{path}: above maximum {schema['maximum']}"

    if isinstance(inst, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in inst:
                return f"{path}: missing required property {name!r}"
        if "propertyNames" in schema:
            for name in inst:
                err = validate(schema["propertyNames"], name, root, f"{path}.{name} (name)")
                if err:
                    return err
        if "maxProperties" in schema and len(inst) > schema["maxProperties"]:
            return f"{path}: more than maxProperties {schema['maxProperties']}"
        if "minProperties" in schema and len(inst) < schema["minProperties"]:
            return f"{path}: fewer than minProperties {schema['minProperties']}"
        ap = schema.get("additionalProperties", None)
        for name, value in inst.items():
            if name in props:
                err = validate(props[name], value, root, f"{path}.{name}")
                if err:
                    return err
            elif isinstance(ap, dict):
                err = validate(ap, value, root, f"{path}.{name}")
                if err:
                    return err
            elif ap is False:
                return f"{path}: additional property {name!r} not allowed (closed vocabulary)"

    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            return f"{path}: fewer than minItems {schema['minItems']}"
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            return f"{path}: more than maxItems {schema['maxItems']}"
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for item in inst:
                key = repr(sorted(item.items())) if isinstance(item, dict) else repr(item)
                if key in seen:
                    return f"{path}: duplicate item {item!r} (uniqueItems)"
                seen.add(key)
        if "items" in schema:
            for i, item in enumerate(inst):
                err = validate(schema["items"], item, root, f"{path}[{i}]")
                if err:
                    return err
        if "contains" in schema:
            if not any(validate(schema["contains"], item, root, f"{path}[contains]") is None for item in inst):
                return f"{path}: no item matches 'contains' {schema['contains']!r}"

    for sub in schema.get("allOf", []):
        err = validate(sub, inst, root, path)
        if err:
            return err
    if "anyOf" in schema:
        if not any(validate(sub, inst, root, path) is None for sub in schema["anyOf"]):
            return f"{path}: no anyOf branch matched"
    if "oneOf" in schema:
        matches = sum(1 for sub in schema["oneOf"] if validate(sub, inst, root, path) is None)
        if matches != 1:
            return f"{path}: oneOf matched {matches} branches (need exactly 1)"
    if "not" in schema:
        if validate(schema["not"], inst, root, path) is None:
            return f"{path}: matched 'not' schema"
    if "if" in schema:
        if validate(schema["if"], inst, root, path) is None:
            if "then" in schema:
                err = validate(schema["then"], inst, root, path)
                if err:
                    return err
        elif "else" in schema:
            err = validate(schema["else"], inst, root, path)
            if err:
                return err
    return None


def validate_manifest(manifest: Mapping[str, Any], schema: Mapping[str, Any]) -> str | None:
    """Validate an extension manifest against the envelope schema.

    ``schema`` is the parsed ``extension-manifest.schema.json`` (the
    caller loads its vendored copy); pass ``schema["$defs"]["bundledManifest"]``
    with the full schema as ``schema`` root for T1 bundled manifests.
    Returns None when structurally valid, else a path-precise error.
    Host-owned lint beyond structure is NOT done here (see module
    docstring) — :func:`secret_fields` and the reserved-prefix check
    below cover the two rules every host applies at registration.
    """
    manifest_dict = dict(manifest)
    err = validate(schema, manifest_dict, schema)
    if err is not None:
        return err
    ext_id = manifest_dict.get("id")
    if isinstance(ext_id, str) and ext_id.startswith(RESERVED_ID_PREFIXES):
        return (
            f"$.id: {ext_id!r} uses a reserved prefix {RESERVED_ID_PREFIXES} "
            "(host-/neuro- are family-internal)"
        )
    return None


#: Static pattern-safety bounds (mirror desktop safePattern.ts / audit F9).
MAX_SINGLE_REPETITION = 1000
MAX_REPETITION_PRODUCT = 100_000


def is_timeout_safe_pattern(source: str) -> str | None:
    """Return None when a manifest-supplied regex is timeout-safe, else a
    precise reason (audit 2026-10-18 F9; desktop's ``safePattern.ts`` port).

    Refuses the ambiguous backtracking classes at registration: a
    quantifier iterating more than once over a group that already contains
    a quantifier (star height >= 2), backreferences, quantifiers inside
    lookarounds, counted repetitions > 1000, and nested counted products
    > 100000. Fail-closed: a false positive is an authoring inconvenience,
    never a security risk; the host also caps the subject length.
    """
    if not isinstance(source, str):
        return "pattern must be a string"
    frames: list[dict[str, Any]] = [
        {"contains": False, "lookaround": False, "product": 1, "alternation": False}
    ]
    last_atom: dict[str, bool] | None = None
    index = 0
    length = len(source)

    def quantify(max_repeat: float, min_repeat: int, counted: bool) -> str | None:
        nonlocal last_atom
        if last_atom is None:
            return "quantifier with nothing to repeat"
        if counted and max_repeat > MAX_SINGLE_REPETITION:
            return f"a counted repetition exceeds {MAX_SINGLE_REPETITION}"
        if last_atom["group"] and last_atom.get("alternation") and max_repeat > 1:
            return (
                "a quantifier iterating more than once applies to a group with "
                "alternation (ambiguous backtracking)"
            )
        if last_atom["group"] and last_atom["contains"] and max_repeat > 1:
            return (
                "a quantifier iterating more than once applies to a group that "
                "already contains one (star height >= 2)"
            )
        frame = frames[-1]
        if max_repeat > 1 or min_repeat > 1:
            frame["contains"] = True
        if counted:
            frame["product"] *= min(max_repeat, MAX_REPETITION_PRODUCT)
            if frame["product"] > MAX_REPETITION_PRODUCT:
                return (
                    f"nested counted repetitions exceed the "
                    f"{MAX_REPETITION_PRODUCT} product cap"
                )
        return None

    while index < length:
        char = source[index]
        if char == "\\":
            escaped = source[index + 1] if index + 1 < length else ""
            if escaped.isdigit() and escaped != "0":
                return "backreferences are refused"
            index += 2
            last_atom = {"group": False, "contains": False}
            continue
        if char == "[":
            cursor = index + 1
            if cursor < length and source[cursor] == "^":
                cursor += 1
            if cursor < length and source[cursor] == "]":
                cursor += 1
            while cursor < length and source[cursor] != "]":
                cursor += 2 if source[cursor] == "\\" else 1
            index = cursor + 1
            last_atom = {"group": False, "contains": False}
            continue
        if char == "(":
            frame: dict[str, Any] = {
                "contains": False,
                "lookaround": False,
                "product": 1,
                "alternation": False,
            }
            cursor = index + 1
            if cursor < length and source[cursor] == "?":
                kind = source[cursor + 1] if cursor + 1 < length else ""
                if kind in ("=", "!"):
                    frame["lookaround"] = True
                    cursor += 2
                elif kind == "<":
                    after = source[cursor + 2] if cursor + 2 < length else ""
                    if after in ("=", "!"):
                        frame["lookaround"] = True
                        cursor += 3
                    else:
                        cursor += 2
                        while cursor < length and source[cursor] != ">":
                            cursor += 1
                        cursor += 1
                elif kind == ":":
                    cursor += 2
            frames.append(frame)
            index = cursor
            last_atom = None
            continue
        if char == ")":
            if len(frames) <= 1:
                return "unbalanced group"
            frame = frames.pop()
            if frame["lookaround"] and frame["contains"]:
                return "quantifiers inside lookarounds are refused"
            index += 1
            last_atom = {
                "group": True,
                "contains": frame["contains"],
                "alternation": frame["alternation"],
            }
            continue
        if char == "|":
            frames[-1]["alternation"] = True
            index += 1
            last_atom = None
            continue
        if char in ("*", "+"):
            reason = quantify(float("inf"), 1, False)
            if reason:
                return reason
            index += 1
            if index < length and source[index] == "?":
                index += 1
            last_atom = None
            continue
        if char == "?":
            reason = quantify(1, 1, False)
            if reason:
                return reason
            index += 2 if index + 1 < length and source[index + 1] == "?" else 1
            last_atom = None
            continue
        if char == "{":
            cursor = index + 1
            min_text = ""
            while cursor < length and source[cursor].isdigit():
                min_text += source[cursor]
                cursor += 1
            if min_text:
                max_text = min_text
                if cursor < length and source[cursor] == ",":
                    cursor += 1
                    max_text = ""
                    while cursor < length and source[cursor].isdigit():
                        max_text += source[cursor]
                        cursor += 1
                if cursor < length and source[cursor] == "}":
                    max_repeat = (
                        float("inf") if max_text == "" else int(max_text)
                    )
                    reason = quantify(max_repeat, int(min_text), True)
                    if reason:
                        return reason
                    index = cursor + 1
                    if index < length and source[index] == "?":
                        index += 1
                    last_atom = None
                    continue
        index += 1
        last_atom = {"group": False, "contains": False}
    if len(frames) != 1:
        return "unbalanced group"
    return None


def validate_registration(
    manifest: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    bundled: bool,
    profile: Mapping[str, Any] | None = None,
    tier: str | None = None,
    docs_root: str | Path | None = None,
    docs_schema: Mapping[str, Any] | None = None,
    charter: Mapping[str, Any] | None = None,
    byte_size: int | None = None,
    kind_validator: Callable[[Mapping[str, Any]], list[str]] | None = None,
) -> list[str]:
    """The one registration decision every host runs (plan 37 Phase 1).

    Returns every problem (empty = registered). Composes structure, the
    reserved-id rule, the cross-key i18n and docs semantics, the host
    profile check, capability cross-references, defaults validation and
    the timeout-safe pattern guard. Host-specific deep validation rides
    ``kind_validator`` (for example the mini-app pack schema).
    """
    document = dict(manifest)
    problems: list[str] = []
    target = schema["$defs"]["bundledManifest"] if bundled else schema
    err = validate(target, document, schema)
    if err is not None:
        problems.append(err)
        # Structure is the gate; semantics on malformed documents only add
        # noise. Return the structural reason(s) alone.
        return problems
    ext_id = document.get("id")
    if isinstance(ext_id, str) and ext_id.startswith(RESERVED_ID_PREFIXES):
        problems.append(
            f"$.id: {ext_id!r} uses a reserved prefix {RESERVED_ID_PREFIXES} "
            "(host-/neuro- are family-internal)"
        )

    problems.extend(manifest_i18n_semantics(document))

    limits = (charter or {}).get("limits") or {}
    max_bytes = limits.get("manifestMaxBytes")
    if isinstance(max_bytes, int) and byte_size is not None and byte_size > max_bytes:
        problems.append(f"manifest exceeds {max_bytes} bytes ({byte_size})")

    pointer = document.get("docs")
    if pointer is not None:
        if pointer != DOCS_POINTER:
            problems.append(
                f"$.docs: must be {DOCS_POINTER!r}, got {pointer!r}"
            )
        elif docs_root is None:
            problems.append("$.docs: the bundle root is required to resolve the docs tree")
        else:
            docs_dir = Path(docs_root) / "docs"
            tree_path = docs_dir / "docs-tree.json"
            try:
                tree = json.loads(tree_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"$.docs: tree is unreadable ({exc})")
            else:
                if docs_schema is not None:
                    tree_err = validate(docs_schema, tree, docs_schema)
                    if tree_err is not None:
                        problems.append(f"$.docs: {tree_err}")
                problems.extend(
                    f"$.docs: {p}"
                    for p in docs_tree_semantics(
                        tree,
                        exists=lambda ref: (docs_dir / ref).is_file(),
                    )
                )

    kind = document.get("kind")
    if kind == "mini-app":
        api_version = document.get("apiVersion")
        if not isinstance(api_version, int) or api_version > 1:
            problems.append(
                f"$.apiVersion: mini-app uses the integer refinement (<= 1), got {api_version!r}"
            )
    else:
        if document.get("apiVersion") != ENVELOPE_API_VERSION:
            problems.append(
                f"$.apiVersion: must be {ENVELOPE_API_VERSION!r}, got {document.get('apiVersion')!r}"
            )
        kind_version = document.get("kindVersion")
        if not isinstance(kind_version, int) or kind_version > CONNECTOR_KIND_VERSION:
            problems.append(
                f"$.kindVersion: host supports <= {CONNECTOR_KIND_VERSION}, got {kind_version!r}"
            )

    capabilities = [str(c) for c in (document.get("capabilities") or [])]
    if "fs-write-mutate" in capabilities:
        problems.append(
            "$.capabilities: fs-write-mutate is reserved for refinement >= 2 "
            "(ADR-0029 apiVersion >= 2 lineage) and is rejected in v1"
        )
    if "ui:custom-code" in capabilities and not bundled:
        problems.append(
            "$.capabilities: ui:custom-code is bundled-only (T1 reviewed code); "
            "an imported manifest can never declare it"
        )
    domains = document.get("domains") or []
    if "net" in capabilities and not domains:
        problems.append("$.domains: the net capability requires declared domains")
    tools = document.get("tools") or []
    if "chat-tools" in capabilities and not tools:
        problems.append("$.tools: the chat-tools capability requires tool contributions")

    defaults = document.get("defaults")
    config_schema = document.get("configSchema")
    if isinstance(defaults, dict) and isinstance(config_schema, dict):
        default_err = validate(config_schema, defaults, config_schema)
        if default_err is not None:
            problems.append(f"$.defaults: {default_err}")

    def _pattern_problems(node: Any, path: str) -> list[str]:
        found: list[str] = []
        if not isinstance(node, dict):
            return found
        props = node.get("properties")
        if isinstance(props, dict):
            for name, field in props.items():
                if not isinstance(field, dict):
                    continue
                pattern = field.get("pattern")
                if isinstance(pattern, str):
                    reason = is_timeout_safe_pattern(pattern)
                    if reason is not None:
                        found.append(f"{path}.{name}.pattern: {reason}")
        return found

    problems.extend(_pattern_problems(config_schema, "$.configSchema"))
    problems.extend(_pattern_problems(document.get("itemSchema"), "$.itemSchema"))

    if profile is not None:
        kinds = profile.get("kinds")
        if isinstance(kinds, list) and kind not in kinds:
            problems.append(
                f"$.kind: {kind!r} is not hosted by this profile (hosts: {', '.join(kinds)})"
            )
        tiers = profile.get("tiers")
        if tier is not None and isinstance(tiers, list) and tier not in tiers:
            problems.append(
                f"tier {tier!r} is not hosted by this profile (tiers: {', '.join(tiers)})"
            )
        honors = profile.get("honors")
        if isinstance(honors, list):
            unhonored = sorted(c for c in capabilities if c not in honors)
            if unhonored:
                problems.append(
                    "capabilities not honored by this host profile: "
                    + ", ".join(unhonored)
                )

    if kind_validator is not None:
        problems.extend(kind_validator(document))

    # Stable, de-duplicated order.
    seen: set[str] = set()
    unique: list[str] = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique


def run_manifest_fixtures(
    fixtures: Mapping[str, Any],
    schema: Mapping[str, Any],
    sources_schema: Mapping[str, Any],
) -> list[str]:
    """Run the canonical manifest/bundled/source fixture wall (plan 37
    Phase 0.3). Hosts call this from their contract test instead of
    re-writing the valid/invalid loop; returns every failure (empty =
    green). The ``reject`` channel is enforced: a ``semantics`` case must
    pass the schema and fail the semantic rules; a ``schema`` case must be
    rejected structurally."""
    failures: list[str] = []
    for group in ("manifest", "bundled", "sources"):
        cases = fixtures.get(group) or {}
        for case in cases.get("valid", []):
            name = case.get("name")
            target, root = _fixture_target(group, case, schema, sources_schema)
            err = validate(target, case.get("doc"), root)
            if err is not None:
                failures.append(f"{group}:{name}: valid case rejected: {err}")
                continue
            if group in ("manifest", "bundled"):
                sem = manifest_i18n_semantics(case.get("doc") or {})
                if sem:
                    failures.append(
                        f"{group}:{name}: valid case rejected by semantics: {sem[0]}"
                    )
        for case in cases.get("invalid", []):
            name = case.get("name")
            target, root = _fixture_target(group, case, schema, sources_schema)
            err = validate(target, case.get("doc"), root)
            channel = case.get("reject", "schema")
            if channel == "semantics":
                if group not in ("manifest", "bundled"):
                    failures.append(
                        f"{group}:{name}: semantics channel outside a manifest group"
                    )
                elif err is not None:
                    failures.append(
                        f"{group}:{name}: semantics channel but the schema rejected it: {err}"
                    )
                elif not manifest_i18n_semantics(case.get("doc") or {}):
                    failures.append(f"{group}:{name}: accepted by the semantics channel")
            elif channel == "schema":
                if err is None:
                    failures.append(f"{group}:{name}: accepted by the schema channel")
            else:
                failures.append(f"{group}:{name}: unknown reject channel {channel!r}")
    return failures


def _fixture_target(
    group: str,
    case: Mapping[str, Any],
    schema: Mapping[str, Any],
    sources_schema: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any]]:
    if group == "sources" and case.get("against") == "desiredState":
        return sources_schema["$defs"]["desiredStateDocument"], sources_schema
    if group == "sources" and case.get("against") == "localSource":
        return sources_schema["$defs"]["localSourceDescriptor"], sources_schema
    if group == "bundled":
        return schema["$defs"]["bundledManifest"], schema
    if group == "sources":
        return sources_schema, sources_schema
    return schema, schema


def secret_fields(config_schema: Mapping[str, Any] | None) -> list[str]:
    """Field names marked ``x-secret: true`` in a configSchema (D19).

    Registration law: a value present for any of these fields in stored
    instance config is a registration-time throw — values live only in
    the settings-map-declared secret store.
    """
    if not config_schema:
        return []
    fields: list[str] = []
    for name, field_schema in (config_schema.get("properties") or {}).items():
        if isinstance(field_schema, dict) and field_schema.get("x-secret") is True:
            fields.append(name)
    return fields


def docs_tree_semantics(    tree: Any,
    exists: Any = None,
) -> list[str]:
    """Wall-equivalent semantics for a docs-tree document beyond its
    schema (plan 34 — dev wall 14's job; every host validating a tree
    runs these beside the schema):

    - node/item ids unique across the whole tree;
    - declared ``chapters`` have matching root node ids (chaptered form);
    - each closed category slug at most once per tree, and ``index:
      true`` on at most one item — the landing rule's override
      (extensionDocsTree form);
    - every ``file`` reference exists (when ``exists`` — a callable
      taking the ref — is provided);
    - every item-noted ``locales`` entry (§10.9 page-locale convention,
      plan 35 wave B) has its ``<page>.<locale>.md`` sibling beside the
      tree (same ``exists`` discipline — a noted locale without its
      sibling is a broken promise, exactly like a missing ``file``).

    Returns a list of problems (empty = clean). A tree that is neither
    form (no ``roots`` and no ``categories``) is itself a problem — the
    schema already rejected it by then.
    """
    problems: list[str] = []
    if not isinstance(tree, dict):
        return ["tree is not an object"]
    if "roots" in tree:
        ids: list[str] = []
        stack = list(tree.get("roots") or [])
        while stack:
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            ids.append(node.get("id"))
            if exists is not None and node.get("file") and not exists(node["file"]):
                problems.append(f"node {node.get('id')!r}: file {node['file']!r} does not resolve")
            stack.extend(node.get("children") or [])
        for chapter in tree.get("chapters") or []:
            if chapter not in ids:
                problems.append(f"declared chapter {chapter!r} has no matching node id")
    else:
        ids = []
        slugs: list[str] = []
        index_claims: list[str] = []
        for category in tree.get("categories") or []:
            if not isinstance(category, dict):
                continue
            slugs.append(category.get("category"))
            for item in category.get("items") or []:
                if not isinstance(item, dict):
                    continue
                ids.append(item.get("id"))
                if item.get("index"):
                    index_claims.append(str(item.get("id")))
                if exists is not None and item.get("file") and not exists(item["file"]):
                    problems.append(f"item {item.get('id')!r}: file {item['file']!r} does not resolve")
                # §10.9 (plan 35 wave B): a noted locale needs its sibling
                if exists is not None and isinstance(item.get("locales"), list) and isinstance(item.get("file"), str):
                    base = item["file"][:-3] if item["file"].endswith(".md") else item["file"]
                    for locale in item["locales"]:
                        sibling = f"{base}.{locale}.md"
                        if not exists(sibling):
                            problems.append(
                                f"item {item.get('id')!r}: notes locale {locale!r} "
                                f"but the sibling {sibling!r} does not resolve"
                            )
        dup_slugs = sorted({s for s in slugs if slugs.count(s) > 1})
        if dup_slugs:
            problems.append(
                f"duplicate category slugs {dup_slugs} "
                "(each closed category appears at most once)"
            )
        if len(index_claims) > 1:
            problems.append(
                f"index: true on more than one item {index_claims} "
                "(the landing rule allows exactly one)"
            )
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate node ids {dupes}")
    return problems


#: The i18n field caps the envelope schema declares structurally
#: (mirrors extension-charter.json limits — hosts may tighten, never
#: loosen). The default-locale title REQUIREMENT is cross-key and lives
#: in the semantics below, not in JSON Schema.
I18N_TITLE_MAX_CHARS = 80
I18N_SUMMARY_MAX_CHARS = 200


def manifest_i18n_semantics(
    manifest: Mapping[str, Any],
) -> list[str]:
    """Wall-equivalent semantics for a manifest's ``i18n`` block beyond
    its schema (owner decision 2026-10-19, plan 35 wave B — every host
    validating a manifest runs these beside the schema):

    - the DEFAULT-LOCALE entry carries a ``title`` (1..80 chars) — the
      rule that makes a hub's title-fallback-to-id unreachable for
      valid manifests; other locales may omit it;
    - a ``title`` present in ANY entry is 1..80 chars;
    - a ``summary`` present in any entry is <= 200 chars (the schema's
      structural cap, mirrored here so a semantics-only caller stays
      exact).

    Returns a list of problems (empty = clean). Structure (types,
    lengths of undeclared keys, locale syntax) is the schema's job —
    this check is only the cross-key rule plus the declared caps.
    """
    problems: list[str] = []
    i18n = manifest.get("i18n")
    if not isinstance(i18n, dict):
        return ["i18n is not an object (the schema rejects it before semantics)"]
    default_locale = i18n.get("defaultLocale")
    locales = i18n.get("locales")
    if not isinstance(default_locale, str) or not isinstance(locales, dict):
        return ["i18n.defaultLocale/locales malformed (the schema rejects it before semantics)"]
    default_entry = locales.get(default_locale)
    if not isinstance(default_entry, dict):
        problems.append(f"i18n.locales carries no entry for the default locale {default_locale!r}")
        return problems
    title = default_entry.get("title")
    if title is None:
        problems.append(
            f"i18n.locales[{default_locale!r}]: missing 'title' — the default-locale "
            "title is REQUIRED (cards render it verbatim; there is no honest id fallback)"
        )
    elif not isinstance(title, str):
        problems.append(f"i18n.locales[{default_locale!r}].title: not a string")
    elif not 1 <= len(title) <= I18N_TITLE_MAX_CHARS:
        problems.append(
            f"i18n.locales[{default_locale!r}].title: length {len(title)} outside 1..{I18N_TITLE_MAX_CHARS}"
        )
    for locale, entry in sorted(locales.items()):
        if not isinstance(entry, dict):
            continue
        loc_title = entry.get("title")
        if isinstance(loc_title, str) and not 1 <= len(loc_title) <= I18N_TITLE_MAX_CHARS:
            problems.append(
                f"i18n.locales[{locale!r}].title: length {len(loc_title)} outside 1..{I18N_TITLE_MAX_CHARS}"
            )
        summary = entry.get("summary")
        if isinstance(summary, str) and len(summary) > I18N_SUMMARY_MAX_CHARS:
            problems.append(
                f"i18n.locales[{locale!r}].summary: length {len(summary)} > {I18N_SUMMARY_MAX_CHARS}"
            )
    return problems

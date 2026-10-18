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

import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "ENVELOPE_API_VERSION",
    "KINDS",
    "CONNECTOR_KIND_VERSION",
    "DOCS_POINTER",
    "RESERVED_ID_PREFIXES",
    "docs_tree_semantics",
    "secret_fields",
    "validate",
    "validate_manifest",
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


def docs_tree_semantics(
    tree: Any,
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
      taking the ref — is provided).

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

"""Extension registry — the host profile (family extension charter).

Bundled T1 extensions live one directory per id under ``builtin/``:
``manifest.json`` (the charter envelope, validated AT REGISTRATION against
the vendored schema — an invalid manifest never enters the registry),
``provider.py`` + ``config_flow.py``. Discovery is filesystem drop-in;
integrations are ENABLED BY DEFAULT (admin-curated host profile): a domain
is only skipped when a SYSTEM_ADMIN has written an explicit
``is_enabled=False`` row.

Capability law (D3/D18): what an extension may do is DECLARED in its
manifest — ``capabilities`` (the closed charter taxonomy) and the
connector-kind ``itemSchema`` property names (this host's data-class
profile: the FHIR-shaped item contracts the engine can pull). The
``supports_*`` probing era is over — the engine asks the registry, and an
undeclared capability is never called.

The registry keys extensions by their envelope ``id`` (hyphenated, the
charter form). Health's historical underscore ``domain`` — the package
name, the REST path segment, the ``UserIntegration.provider`` value —
survives as the product alias (owner ruling 4): it derives mechanically
from the id and every lookup accepts either form.
"""

import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions.sdk.base import BaseConfigFlow, BaseHealthProvider
from app.models.system_integration import SystemIntegration

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "extension-manifest.schema.json"
_DOCS_TREE_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "docs-tree.schema.json"


def domain_of(extension_id: str) -> str:
    """The product alias of an envelope id (``dev-dummy`` → ``dev_dummy``)."""
    return extension_id.replace("-", "_")


def _id_of(domain: str) -> str:
    """The envelope id of a product domain (inverse of :func:`domain_of`)."""
    return domain.replace("_", "-")


class IntegrationRegistry:
    def __init__(self):
        self._providers: dict[str, BaseHealthProvider] = {}
        self._config_flows: dict[str, BaseConfigFlow] = {}
        self._manifests: dict[str, dict] = {}
        self._base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")

    async def initialize(self, db: AsyncSession):
        logger.info("Initializing extension registry...")
        self._load_manifests()

        stmt = select(SystemIntegration)
        result = await db.execute(stmt)
        system_integrations = result.scalars().all()

        system_config_by_domain: dict[str, dict] = {
            str(si.domain): dict(si.global_config or {}) for si in system_integrations
        }

        # Extensions are ENABLED BY DEFAULT (host-profile policy): a domain
        # is only skipped when a SYSTEM_ADMIN has explicitly written an
        # is_enabled=False row via the admin console. Missing row == available.
        disabled_domains = {si.domain for si in system_integrations if not si.is_enabled}

        for domain, _manifest in self._manifests.items():
            if domain in disabled_domains:
                logger.debug(
                    f"Extension {domain} is discovered but explicitly "
                    "disabled in system_integrations."
                )
                continue

            await self._load_integration(
                domain, system_config=system_config_by_domain.get(domain, {})
            )

    def _load_manifests(self):
        from app.extensions import contract

        try:
            schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"extension manifest schema unavailable at {_SCHEMA_PATH}: {e} — "
                "registration fails closed (vendored contract set incomplete)"
            ) from e
        try:
            docs_tree_schema = json.loads(_DOCS_TREE_SCHEMA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"docs-tree schema unavailable at {_DOCS_TREE_SCHEMA_PATH}: {e} — "
                "registration fails closed (vendored contract set incomplete)"
            ) from e

        from app.extensions import docs as ext_docs

        for item in sorted(os.listdir(self._base_path)):
            item_path = os.path.join(self._base_path, item)
            manifest_path = os.path.join(item_path, "manifest.json")
            if not os.path.isdir(item_path) or not os.path.exists(manifest_path):
                continue
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                ext_id = manifest.get("id")
                if not ext_id:
                    raise ValueError("manifest has no id")
                # Registration-time validation (the loose-read era ends
                # here): the envelope must validate against the vendored
                # schema or the extension self-disables with a reason —
                # it never half-runs (charter lifecycle law).
                # (Argument order is schema-first — contract.validate(schema, inst);
                # the pre-2026-10-19 call passed (manifest, subschema) and the
                # gate was inert — the same swap career's registry carried.)
                err = contract.validate(schema["$defs"]["bundledManifest"], manifest, schema)
                if err is not None:
                    raise ValueError(f"manifest invalid: {err}")
                problems = contract.manifest_i18n_semantics(manifest)
                if problems:
                    raise ValueError(f"manifest invalid: {'; '.join(problems)}")
                if str(ext_id).startswith(contract.RESERVED_ID_PREFIXES):
                    raise ValueError(f"id {ext_id!r} uses a reserved prefix")
                # Per-extension docs binding (plan 34 §10.5): a manifest
                # carrying the `docs` pointer promises a tree that exists
                # and validates — a broken promise self-disables exactly
                # like an invalid manifest.
                pointer = manifest.get("docs")
                if pointer is not None:
                    if pointer != ext_docs.DOCS_POINTER:
                        raise ValueError(
                            f"docs pointer must be {ext_docs.DOCS_POINTER!r}, got {pointer!r}"
                        )
                    docs_dir = Path(item_path) / "docs"
                    try:
                        tree = ext_docs.load_tree(docs_dir)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"docs tree invalid JSON: {e}") from e
                    if tree is None:
                        raise ValueError(
                            f"manifest points at {ext_docs.DOCS_POINTER} "
                            "but the tree does not exist"
                        )
                    tree_err = ext_docs.validate_tree(tree, docs_dir, docs_tree_schema)
                    if tree_err is not None:
                        raise ValueError(f"docs tree invalid: {tree_err}")
                self._manifests[domain_of(str(ext_id))] = manifest
            except Exception as e:
                logger.error(f"Failed to load manifest for {item}: {e}")

    async def _load_integration(self, domain: str, *, system_config: dict | None = None):
        try:
            provider_module = importlib.import_module(f"app.extensions.builtin.{domain}.provider")
            config_flow_module = importlib.import_module(
                f"app.extensions.builtin.{domain}.config_flow"
            )

            provider_class = self._find_class_by_base(provider_module, BaseHealthProvider)
            config_flow_class = self._find_class_by_base(config_flow_module, BaseConfigFlow)

            if provider_class and config_flow_class:
                provider_instance = provider_class()
                config_flow_instance = config_flow_class()

                # One-time setup with the SystemIntegration.global_config
                # dict (when present) so providers can do system-level
                # resource setup.
                await provider_instance.setup(system_config or {})

                self._gate_secrets(domain, config_flow_instance)

                self._providers[domain] = provider_instance
                self._config_flows[domain] = config_flow_instance
                logger.info(f"Successfully loaded extension: {domain}")
            else:
                logger.warning(f"Could not find required classes in {domain}")

        except Exception as e:
            logger.error(f"Error loading extension {domain}: {e}")

    def _gate_secrets(self, domain: str, config_flow: BaseConfigFlow) -> None:
        """D19 honesty gate: every flow-declared secret field must be
        ``x-secret``-declared in the manifest configSchema — a plaintext
        secret in stored config is a conformance failure, and the two
        declarations may never drift apart."""
        from app.extensions import contract as _contract

        manifest = self._manifests.get(domain) or {}
        declared = set(_contract.secret_fields(manifest.get("configSchema")))
        flow_fields = set(config_flow.get_secret_fields())
        missing = flow_fields - declared
        if missing:
            raise ValueError(
                f"{domain}: secret fields {sorted(missing)} are encrypted by the "
                "config flow but not marked x-secret in the manifest configSchema"
            )

    def _find_class_by_base(self, module: Any, base_class: type) -> type | None:
        for item_name in dir(module):
            item = getattr(module, item_name)
            if (
                isinstance(item, type)
                and issubclass(item, base_class)
                and item is not base_class
                and item.__module__ == module.__name__
            ):
                return item
        return None

    # ------------------------------------------------------------- lookups

    def _canon(self, domain: str) -> str:
        """Accept either the envelope id or the underscore alias."""
        if domain in self._manifests:
            return domain
        alias = domain_of(domain)
        if alias in self._manifests:
            return alias
        return domain

    def get_provider(self, domain: str) -> BaseHealthProvider | None:
        return self._providers.get(self._canon(domain))

    def get_all_providers(self) -> list[BaseHealthProvider]:
        """All loaded provider instances (for generic shutdown / iteration)."""
        return list(self._providers.values())

    def get_config_flow(self, domain: str) -> BaseConfigFlow | None:
        return self._config_flows.get(self._canon(domain))

    def get_manifest(self, domain: str) -> dict | None:
        return self._manifests.get(self._canon(domain))

    def get_all_manifests(self) -> list[dict]:
        return list(self._manifests.values())

    def has_capability(self, domain: str, capability: str) -> bool:
        """Declared-capability gate (D3): the engine's ONLY answer to "can
        this extension do X?". Unloaded/unknown domains declare nothing."""
        manifest = self.get_manifest(domain) or {}
        return capability in (manifest.get("capabilities") or [])

    def declared_data_classes(self, domain: str) -> frozenset[str]:
        """The connector-kind data-class profile: ``itemSchema`` property
        names are the item contracts the engine may pull (observations,
        clinical events, documents, …) — declared data, not probed hooks."""
        manifest = self.get_manifest(domain) or {}
        item_schema = manifest.get("itemSchema") or {}
        return frozenset((item_schema.get("properties") or {}).keys())

    def base_dir(self, domain: str) -> str:
        """Absolute path of a bundled extension's directory (docs, assets)."""
        return os.path.join(self._base_path, domain)


integration_registry = IntegrationRegistry()

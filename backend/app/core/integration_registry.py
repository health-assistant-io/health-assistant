import json
import os
import importlib
import logging
from typing import Dict, Any, Type, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.system_integration import SystemIntegration
from integrations.base import BaseHealthProvider, BaseConfigFlow

logger = logging.getLogger(__name__)


class IntegrationRegistry:
    def __init__(self):
        self._providers: Dict[str, BaseHealthProvider] = {}
        self._config_flows: Dict[str, BaseConfigFlow] = {}
        self._manifests: Dict[str, dict] = {}
        self._base_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "..",
            "integrations",
        )

    async def initialize(self, db: AsyncSession):
        logger.info("Initializing Integration Registry...")
        self._load_manifests()

        # Load system integrations state from DB
        stmt = select(SystemIntegration)
        result = await db.execute(stmt)
        system_integrations = result.scalars().all()

        # Build a per-domain config map from SystemIntegration rows so
        # the registry can hand each provider its system-level config
        # (item 5 of integrations-sdk-improvements). Falls back to an
        # empty dict for newly-enabled integrations that haven't been
        # configured yet.
        system_config_by_domain: dict[str, dict] = {
            si.domain: dict(si.global_config or {})
            for si in system_integrations
        }
        enabled_domains = {si.domain for si in system_integrations if si.is_enabled}

        for domain, manifest in self._manifests.items():
            if domain in enabled_domains:
                await self._load_integration(
                    domain, system_config=system_config_by_domain.get(domain, {})
                )
            else:
                logger.debug(
                    f"Integration {domain} is discovered but not enabled in system_integrations."
                )

    def _load_manifests(self):
        # Scan built-in integrations
        for item in os.listdir(self._base_path):
            item_path = os.path.join(self._base_path, item)
            if os.path.isdir(item_path):
                manifest_path = os.path.join(item_path, "manifest.json")
                if os.path.exists(manifest_path):
                    try:
                        with open(manifest_path, "r") as f:
                            manifest = json.load(f)
                            domain = manifest.get("domain")
                            if domain:
                                self._manifests[domain] = manifest
                    except Exception as e:
                        logger.error(f"Failed to load manifest for {item}: {e}")

    async def _load_integration(self, domain: str, *, system_config: dict | None = None):
        try:
            # Dynamically import the modules
            provider_module = importlib.import_module(f"integrations.{domain}.provider")
            config_flow_module = importlib.import_module(
                f"integrations.{domain}.config_flow"
            )

            # Find the classes
            provider_class = self._find_class_by_base(
                provider_module, BaseHealthProvider
            )
            config_flow_class = self._find_class_by_base(
                config_flow_module, BaseConfigFlow
            )

            if provider_class and config_flow_class:
                provider_instance = provider_class()
                config_flow_instance = config_flow_class()

                # Perform one-time setup. Item 5 of the
                # integrations-sdk-improvements plan: pass the actual
                # SystemIntegration.global_config dict (when present)
                # so providers can do system-level resource setup.
                await provider_instance.setup(system_config or {})

                self._providers[domain] = provider_instance
                self._config_flows[domain] = config_flow_instance
                logger.info(f"Successfully loaded integration: {domain}")
            else:
                logger.warning(f"Could not find required classes in {domain}")

        except Exception as e:
            logger.error(f"Error loading integration {domain}: {e}")

    def _find_class_by_base(self, module: Any, base_class: Type) -> Optional[Type]:
        for item_name in dir(module):
            item = getattr(module, item_name)
            if (
                isinstance(item, type)
                and issubclass(item, base_class)
                and item is not base_class
            ):
                if item.__module__ == module.__name__:
                    return item
        return None

    def get_provider(self, domain: str) -> Optional[BaseHealthProvider]:
        return self._providers.get(domain)

    def get_all_providers(self) -> List[BaseHealthProvider]:
        """All loaded provider instances (for generic shutdown / iteration)."""
        return list(self._providers.values())

    def get_config_flow(self, domain: str) -> Optional[BaseConfigFlow]:
        return self._config_flows.get(domain)

    def get_all_manifests(self) -> List[dict]:
        return list(self._manifests.values())


integration_registry = IntegrationRegistry()

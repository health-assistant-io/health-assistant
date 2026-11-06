"""Family cloud-connector catalog (ADR-0052).

Canonical copy lives in the family standards (``contracts/cloud_providers.py``) and
is vendored byte-identical into the product hosts via
``the family sync tooling`` — edit the canonical copy only,
never a vendored copy (ADR-0031 sync-and-gate).

What this module freezes:

* provider rows — id, label, services (``calendar`` / ``files``), accepted
  auth kinds, transport protocol per service, verbatim OAuth scope sets for
  the ``read`` and ``write`` presets, and the hosts token-bearing calls may
  reach (the polite-runtime allowlist);
* the OAuth endpoints (authorize / token / optional revoke) and extra auth
  parameters, **without** client ids, client secrets or redirect ports —
  those are host/deployment configuration, never catalog data;
* validation helpers that make a malformed catalog a startup error rather
  than a runtime surprise.

Policy stays host-side (ADR-0031): persistence, secret custody, which
account is bound, consent screens, sync schedules and the domain mapping
(what a calendar event means in that product) never live here. This module
is pure data + pure functions over plain values; it imports nothing from
the family products.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
CONTRACT = "cloud-providers"

SERVICES = ("calendar", "files")
AUTH_KINDS = ("oauth2", "app-password", "token", "none")
PROTOCOLS = {
    "calendar": ("caldav", "google-calendar", "graph-calendar", "ics"),
    "files": ("webdav", "google-drive", "graph-drive", "dropbox"),
}
SCOPE_PRESETS = ("read", "write")

#: Closed vocabulary for the bind-form descriptor. A host renders the form
#: from these and never from a provider name (ADR-0052 clause 11).
SETUP_FIELD_KINDS = ("url", "text", "secret", "calendar")

#: The family credential-binding vocabulary (ADR-0042 clause 9 / ADR-0052).
#: A host may store fewer states, never invent more.
ACCOUNT_STATES = (
    "unbound",
    "needs-auth",
    "ok",
    "degraded",
    "failing",
    "needs-secret",
)

#: Family status vocabulary reused for sync runs (ADR-0040 §20).
SYNC_STATUSES = ("ok", "degraded", "failing", "stale")

#: One-liners every host renders verbatim before a provider consent screen.
#: The catalog string is data so products cannot soften or widen it.
CONSENT_NOTICES = {
    "oauth2": (
        "You are delegating your own {provider} account to this app. "
        "The scopes below are exactly what {provider} will show you."
    ),
    "app-password": (
        "This app stores an app-specific password you create at {provider}. "
        "Use a dedicated password and revoke it at {provider} to disconnect."
    ),
    "token": (
        "This app stores an access token for {provider}. "
        "Revoking it at {provider} disconnects this app."
    ),
    "none": "This source needs no credential; it only reads a public feed.",
}


class CatalogError(ValueError):
    """The catalog document is structurally invalid — fail at startup."""


@dataclass(frozen=True)
class OAuthSpec:
    authorize_url: str
    token_url: str
    revoke_url: str | None = None
    extra_auth_params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceSpec:
    service: str
    protocol: str
    writable: bool
    supports_delta: bool
    identity: str
    scopes: Mapping[str, tuple[str, ...]]
    setup_fields: tuple[Mapping[str, Any], ...] = ()
    api_base: str | None = None
    upload_base: str | None = None
    content_base: str | None = None
    path_template: str | None = None

    def scope_set(self, preset: str = "read") -> tuple[str, ...]:
        """Verbatim provider scopes for a preset (``read`` | ``write``)."""
        if preset not in SCOPE_PRESETS:
            raise CatalogError(f"unknown scope preset {preset!r}")
        return tuple(self.scopes.get(preset, ()))

    def effective_scopes(self, preset: str = "read") -> tuple[str, ...]:
        """Verbatim scopes for a preset; ``write`` falls back to ``read``.

        The catalog's ``write`` list is the complete request (a provider such
        as Google lists the read scope there too when the write scope does
        not imply it). Never auto-merge: consent shows exactly what is asked.
        """
        if preset not in SCOPE_PRESETS:
            raise CatalogError(f"unknown scope preset {preset!r}")
        if preset == "write" and self.scopes.get("write"):
            return tuple(self.scopes["write"])
        return tuple(self.scopes.get("read", ()))


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    services: Mapping[str, ServiceSpec]
    auth_kinds: tuple[str, ...]
    base_url_required: bool = False
    oauth: OAuthSpec | None = None
    domains: tuple[str, ...] = ()

    def supports(self, service: str) -> bool:
        return service in self.services

    def service(self, service: str) -> ServiceSpec:
        try:
            return self.services[service]
        except KeyError as exc:  # pragma: no cover - callers validate first
            raise CatalogError(f"provider {self.id!r} has no {service!r} service") from exc

    def oauth_scopes(self, service: str, preset: str = "read") -> tuple[str, ...]:
        return self.service(service).effective_scopes(preset)

    def consent_notice(self, auth_kind: str | None = None) -> str:
        kind = auth_kind or self.auth_kinds[0]
        return CONSENT_NOTICES.get(kind, CONSENT_NOTICES["oauth2"]).format(
            provider=self.label
        )


@dataclass(frozen=True)
class Catalog:
    providers: Mapping[str, Provider]
    order: tuple[str, ...]
    secret_disclosure: str

    def get(self, provider_id: str) -> Provider:
        try:
            return self.providers[provider_id]
        except KeyError as exc:
            raise CatalogError(f"unknown provider {provider_id!r}") from exc

    def for_service(self, service: str) -> tuple[Provider, ...]:
        if service not in SERVICES:
            raise CatalogError(f"unknown service {service!r}")
        return tuple(
            self.providers[provider_id]
            for provider_id in self.order
            if service in self.providers[provider_id].services
        )

    def ids_for_service(self, service: str) -> tuple[str, ...]:
        return tuple(provider.id for provider in self.for_service(service))


def _require_str(row: Mapping[str, Any], key: str, where: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}.{key} must be a non-empty string")
    return value


def _str_tuple(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CatalogError(f"{where} must be a list of strings")
    return tuple(value)


def parse_catalog(document: Mapping[str, Any]) -> Catalog:
    """Validate and freeze a catalog document (raises :class:`CatalogError`)."""
    if not isinstance(document, Mapping):
        raise CatalogError("catalog must be an object")
    if int(document.get("schemaVersion", 0)) != SCHEMA_VERSION:
        raise CatalogError(f"unsupported schemaVersion {document.get('schemaVersion')!r}")
    raw_providers = document.get("providers")
    if not isinstance(raw_providers, list) or not raw_providers:
        raise CatalogError("providers must be a non-empty list")

    protocols = document.get("protocols", PROTOCOLS)
    for service, allowed in PROTOCOLS.items():
        declared = protocols.get(service, allowed) if isinstance(protocols, Mapping) else allowed
        if tuple(declared) != tuple(allowed):
            raise CatalogError(
                f"protocols.{service} must be exactly {list(allowed)} (append via a new schemaVersion)"
            )

    providers: dict[str, Provider] = {}
    order: list[str] = []
    for index, raw in enumerate(raw_providers):
        if not isinstance(raw, Mapping):
            raise CatalogError(f"providers[{index}] must be an object")
        where = f"providers[{index}]"
        provider_id = _require_str(raw, "id", where)
        if provider_id in providers:
            raise CatalogError(f"duplicate provider id {provider_id!r}")
        label = _require_str(raw, "label", where)

        services_raw = raw.get("serviceConfig")
        if not isinstance(services_raw, Mapping):
            raise CatalogError(f"{where}.serviceConfig must be an object")
        declared_services = _str_tuple(raw.get("services"), f"{where}.services")
        if not declared_services:
            raise CatalogError(f"{where}.services must be non-empty")
        if set(declared_services) - set(SERVICES):
            raise CatalogError(f"{where}.services contains an unknown service")

        services: dict[str, ServiceSpec] = {}
        for service in declared_services:
            spec_raw = services_raw.get(service)
            if not isinstance(spec_raw, Mapping):
                raise CatalogError(f"{where}.serviceConfig.{service} missing")
            spec_where = f"{where}.serviceConfig.{service}"
            protocol = _require_str(spec_raw, "protocol", spec_where)
            if protocol not in PROTOCOLS[service]:
                raise CatalogError(
                    f"{spec_where}.protocol {protocol!r} is not in {list(PROTOCOLS[service])}"
                )
            scopes_raw = spec_raw.get("scopes")
            if not isinstance(scopes_raw, Mapping):
                raise CatalogError(f"{spec_where}.scopes must be an object")
            scopes: dict[str, tuple[str, ...]] = {}
            for preset in SCOPE_PRESETS:
                scopes[preset] = _str_tuple(
                    scopes_raw.get(preset, []), f"{spec_where}.scopes.{preset}"
                )
            if protocol in ("caldav", "webdav", "ics") and any(scopes.values()):
                raise CatalogError(
                    f"{spec_where}.scopes must be empty for password/token protocols"
                )
            if protocol in ("google-calendar", "google-drive", "graph-calendar",
                            "graph-drive", "dropbox") and not scopes["read"]:
                raise CatalogError(f"{spec_where}.scopes.read must not be empty for OAuth")
            setup_raw = spec_raw.get("setupFields", [])
            if not isinstance(setup_raw, list):
                raise CatalogError(f"{spec_where}.setupFields must be a list")
            setup_fields: list[Mapping[str, Any]] = []
            for field_index, field_raw in enumerate(setup_raw):
                field_where = f"{spec_where}.setupFields[{field_index}]"
                if not isinstance(field_raw, Mapping):
                    raise CatalogError(f"{field_where} must be an object")
                kind = _require_str(field_raw, "kind", field_where)
                if kind not in SETUP_FIELD_KINDS:
                    raise CatalogError(
                        f"{field_where}.kind {kind!r} is not in {list(SETUP_FIELD_KINDS)}"
                    )
                key = _require_str(field_raw, "key", field_where)
                label_key = _require_str(field_raw, "labelKey", field_where)
                auth_kind = field_raw.get("authKind")
                if auth_kind is not None and auth_kind not in AUTH_KINDS:
                    raise CatalogError(f"{field_where}.authKind must use {list(AUTH_KINDS)}")
                setup_fields.append(
                    {
                        "key": key,
                        "kind": kind,
                        "required": bool(field_raw.get("required", False)),
                        "labelKey": label_key,
                        "placeholder": field_raw.get("placeholder"),
                        "autocomplete": field_raw.get("autocomplete"),
                        "authKind": auth_kind,
                        "manualKey": field_raw.get("manualKey"),
                    }
                )
            services[service] = ServiceSpec(
                service=service,
                protocol=protocol,
                writable=bool(spec_raw.get("writable", False)),
                supports_delta=bool(spec_raw.get("supportsDelta", False)),
                identity=str(spec_raw.get("identity", "")),
                scopes=scopes,
                setup_fields=tuple(setup_fields),
                api_base=spec_raw.get("apiBase"),
                upload_base=spec_raw.get("uploadBase"),
                content_base=spec_raw.get("contentBase"),
                path_template=spec_raw.get("pathTemplate"),
            )

        auth_kinds = _str_tuple(raw.get("authKinds"), f"{where}.authKinds")
        if not auth_kinds or set(auth_kinds) - set(AUTH_KINDS):
            raise CatalogError(f"{where}.authKinds must use {list(AUTH_KINDS)}")
        oauth_raw = raw.get("oauth")
        oauth = None
        if "oauth2" in auth_kinds:
            if not isinstance(oauth_raw, Mapping):
                raise CatalogError(f"{where}.oauth is required for oauth2 providers")
            oauth = OAuthSpec(
                authorize_url=_require_str(oauth_raw, "authorizeUrl", f"{where}.oauth"),
                token_url=_require_str(oauth_raw, "tokenUrl", f"{where}.oauth"),
                revoke_url=oauth_raw.get("revokeUrl") or None,
                extra_auth_params={
                    str(key): str(value)
                    for key, value in (oauth_raw.get("extraAuthParams") or {}).items()
                },
            )

        domains = _str_tuple(raw.get("domains"), f"{where}.domains")
        if any("/" in domain or " " in domain for domain in domains):
            raise CatalogError(f"{where}.domains must be bare hostnames")
        providers[provider_id] = Provider(
            id=provider_id,
            label=label,
            services=services,
            auth_kinds=auth_kinds,
            base_url_required=bool(raw.get("baseUrlRequired", False)),
            oauth=oauth,
            domains=domains,
        )
        order.append(provider_id)

    disclosure = document.get("secretDisclosure")
    if not isinstance(disclosure, str) or not disclosure.strip():
        raise CatalogError("secretDisclosure must be a non-empty string")
    return Catalog(providers=providers, order=tuple(order), secret_disclosure=disclosure)


def load_catalog(path: str | Path) -> Catalog:
    return parse_catalog(json.loads(Path(path).read_text(encoding="utf-8")))


def catalog_path() -> Path:
    """Default catalog beside this module (vendored hosts get it via sync)."""
    return Path(__file__).with_name("cloud-providers.json")


def default_catalog() -> Catalog:
    return load_catalog(catalog_path())


def host_allowed(provider: Provider, host: str) -> bool:
    """Whether a token-bearing call to ``host`` stays inside the grant.

    Providers without declared domains are self-hosted (the account's base
    URL is the scope, validated by the host's polite runtime); the catalog
    can then not narrow it and the caller must enforce its own allowlist.
    """
    if not provider.domains:
        return True
    host = (host or "").split(":", 1)[0].lower()
    for domain in provider.domains:
        domain = domain.lower()
        if host == domain or host.endswith("." + domain):
            return True
    return False


def scope_presets_for(provider: Provider, service: str) -> dict[str, tuple[str, ...]]:
    spec = provider.service(service)
    return {preset: spec.effective_scopes(preset) for preset in SCOPE_PRESETS}


def scope_growth(
    granted: Iterable[str], requested: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(added, removed)`` between a stored grant and a request.

    Consent law: ``added`` forces a fresh consent screen, ``removed`` must
    be shown (a re-bind may not silently shrink a grant either).
    """
    granted_list, requested_list = list(granted), list(requested)
    granted_set, requested_set = set(granted_list), set(requested_list)
    # Preserve the caller's order: consent screens render these lists, and a
    # set difference would make the copy non-deterministic across runs.
    added = tuple(scope for scope in requested_list if scope not in granted_set)
    removed = tuple(scope for scope in granted_list if scope not in requested_set)
    return added, removed


def resolve_provider(catalog: Catalog, provider_id: str, service: str) -> Provider:
    provider = catalog.get(provider_id)
    if not provider.supports(service):
        raise CatalogError(f"provider {provider_id!r} does not expose {service!r}")
    return provider


def provider_rows(catalog: Catalog, service: str) -> list[dict[str, Any]]:
    """Serializable rows for a settings surface (never credential material)."""
    rows: list[dict[str, Any]] = []
    for provider in catalog.for_service(service):
        spec = provider.service(service)
        rows.append(
            {
                "id": provider.id,
                "label": provider.label,
                "authKinds": list(provider.auth_kinds),
                "baseUrlRequired": provider.base_url_required,
                "protocol": spec.protocol,
                "writable": spec.writable,
                "supportsDelta": spec.supports_delta,
                "scopePresets": {
                    preset: list(spec.effective_scopes(preset))
                    for preset in SCOPE_PRESETS
                },
                "consentNotice": provider.consent_notice(),
                # Health's review: the notice must be selected by the CHOSEN
                # auth kind, never the first one — a token ICS bind must not
                # read "needs no credential".
                "consentNotices": {
                    kind: provider.consent_notice(kind) for kind in provider.auth_kinds
                },
                # Rev 6: the bind form renders ONLY from this (never a
                # provider-name branch in host UI).
                "setupFields": [dict(field) for field in spec.setup_fields],
                "domains": list(provider.domains),
            }
        )
    return rows


def client_id_env(provider_id: str, *, prefix: str) -> str:
    """The env var a host reads for this provider's public OAuth client id.

    Convention as data (desktop's review): ``{PREFIX}_{ID}_CLIENT_ID`` with the
    provider id uppercased and hyphenated to underscores. Never a per-provider
    lookup table — that is the hardcoding this contract exists to remove.
    """
    token = provider_id.upper().replace("-", "_")
    return f"{prefix.upper()}_{token}_CLIENT_ID"


def build_base_url(provider: Provider, service: str, base_url: str | None, username: str | None) -> str:
    """Resolve the collection root for a password/token protocol provider."""
    spec = provider.service(service)
    if spec.api_base:
        root = spec.api_base
    else:
        if not base_url:
            raise CatalogError(f"provider {provider.id!r} requires a base URL")
        root = base_url
    root = root.rstrip("/")
    template = spec.path_template or ""
    if template:
        if "{username}" in template and not username:
            raise CatalogError(f"provider {provider.id!r} requires a username")
        path = template.replace("{username}", (username or "").strip("/"))
        root = f"{root}{path}"
    return root.rstrip("/") + "/"


__all__ = [
    "ACCOUNT_STATES",
    "AUTH_KINDS",
    "CONSENT_NOTICES",
    "CONTRACT",
    "Catalog",
    "CatalogError",
    "OAuthSpec",
    "SETUP_FIELD_KINDS",
    "client_id_env",
    "PROTOCOLS",
    "Provider",
    "SCHEMA_VERSION",
    "SCOPE_PRESETS",
    "SERVICES",
    "SYNC_STATUSES",
    "ServiceSpec",
    "build_base_url",
    "catalog_path",
    "default_catalog",
    "host_allowed",
    "load_catalog",
    "parse_catalog",
    "provider_rows",
    "resolve_provider",
    "scope_growth",
    "scope_presets_for",
]

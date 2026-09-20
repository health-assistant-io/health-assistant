"""§15 BYOK one-click provider setup — USER-scope orchestration.

Uniform family semantics (dev/guidelines/ai-features.md §15, frozen
2026-09-19; Python reference: study ``app/ai/providers/setup.py``):

* fetch-first validation — the key is validated by fetching the vendor's
  real model catalog under a timeout; a classified failure persists nothing;
* curated allowlists matched exact-or-snapshot-suffix; zero preset matches
  fall back to the full catalog with an explicit ``curated_missed`` signal;
* append-union on re-run (dedupe by wire id); user models are never deleted;
* gap-fill only empty/dead slots — live assignments are never clobbered;
* ``preset_key`` stamp + adoption of manual rows (same type+base, earliest
  first); options body ``curated_ids / bind_chat / bind_vision / bind_stt``.

Health deltas (recorded per plan 17 Phase 3):

* **USER scope only.** Every row this module creates or touches is
  ``scope=USER`` bound to ``(tenant_id, user_id)`` — SYSTEM/TENANT
  providers and assignments are never read for adoption and never written.
* **Slot mapping.** The family ``chat``/text slot is health's ``default``
  task (the resolution fallback), the ``vision`` slot is ``ocr``, and the
  ``stt`` slot is ``transcription``. Health has no embeddings slot.
* **Secrets** follow health's existing encrypted-at-rest pattern
  (``app.core.encryption`` on ``ai_providers.api_key``) — never plaintext,
  never logged.
* ``api_base`` is SSRF-guarded (``guard_api_base``) exactly like the manual
  create/update path.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.capabilities import required_capabilities_for_task
from app.ai.providers.enums import TaskType
from app.ai.providers.errors import ClassifiedProviderError, classify_provider_error
from app.ai.providers.presets import SETUP_PRESETS
from app.ai.providers.service import guard_api_base
from app.core.encryption import decrypt_secret, encrypt_secret
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AITaskAssignment,
    AIScope,
)

logger = logging.getLogger(__name__)

#: Family slot → health's USER-scope task slot (plan 17 Phase 3 delta).
_SLOT_TASK_TYPES: dict[str, str] = {
    "chat": TaskType.DEFAULT.value,
    "vision": TaskType.OCR.value,
    "stt": TaskType.TRANSCRIPTION.value,
}


class UnknownPresetError(Exception):
    """The preset key is not on health's enabled setup surface."""


class CrossProviderModelError(Exception):
    """set-default was handed a model that belongs to another provider."""


class SetupError(Exception):
    """A classified, client-routable setup failure (nothing persisted)."""

    def __init__(self, classified: ClassifiedProviderError, vendor_message: str) -> None:
        super().__init__(vendor_message)
        self.classified = classified
        self.vendor_message = vendor_message


@dataclass
class SetupOptions:
    curated_ids: Optional[list[str]] = None
    bind_chat: bool = True
    bind_vision: bool = True
    bind_stt: bool = True


@dataclass(frozen=True)
class SetupOutcome:
    provider_id: Any
    catalog_count: int
    curated_missed: bool
    assigned_chat_model: Optional[str] = None
    assigned_vision_model: Optional[str] = None
    assigned_stt_model: Optional[str] = None


@dataclass
class _PersistedModel:
    external_id: str
    caps: list[str]
    model_id: Any = field(default=None)


# ---------------------------------------------------------------------------
# Catalog fetch + capability inference (§15 vocabulary)
# ---------------------------------------------------------------------------


def infer_caps(external_id: str, methods: Optional[list[str]] = None) -> list[str]:
    """Infer the §15 capability set from a model id (optional Gemini methods)."""
    name = external_id.lower()
    if "embedding" in name or "bge" in name or (
        methods is not None and "embedContent" in methods
    ):
        return ["embeddings"]
    if any(hint in name for hint in ("whisper", "transcribe", "stt")):
        return ["stt"]
    if any(hint in name for hint in ("tts", "speech", "voice")):
        return ["tts"]
    if methods is not None and "generateContent" not in methods:
        return []
    caps = ["text"]
    vision_hints = (
        "gemini", "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "claude-3",
        "claude-4", "claude-sonnet", "claude-opus", "claude-haiku", "vision",
        "-vl", "llava", "pixtral", "gemma3",
    )
    if any(hint in name for hint in vision_hints):
        caps.append("vision")
    tool_hints = (
        "gpt-4", "gpt-5", "o3", "o4", "claude", "gemini", "deepseek", "qwen",
        "llama-3", "mistral",
    )
    if any(hint in name for hint in tool_hints):
        caps.append("tools")
    return caps


@dataclass(frozen=True)
class RemoteModel:
    external_id: str
    caps: tuple[str, ...]


async def fetch_remote_models(
    wire_type: str,
    base_url: str,
    api_key: Optional[str],
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> list[RemoteModel]:
    """Fetch the vendor's model catalog for a canonical §15 wire type.

    Raises ``httpx.HTTPStatusError``/``httpx.TransportError``/timeout —
    classification happens in :func:`classify_provider_error`.
    """
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        if wire_type == "google":
            response = await client.get(
                f"{base_url}/v1beta/models",
                params={"key": api_key, "pageSize": 200},
            )
            response.raise_for_status()
            items: list[dict[str, Any]] = response.json().get("models", [])
            models = []
            for item in items:
                external_id = item.get("name", "").removeprefix("models/")
                if not external_id:
                    continue
                methods = item.get("supportedGenerationMethods", [])
                caps = infer_caps(external_id, methods)
                if caps:
                    models.append(RemoteModel(external_id=external_id, caps=tuple(caps)))
            return models
        if wire_type == "openai_compatible":
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            data = response.json().get("data", [])
            return [
                RemoteModel(external_id=item["id"], caps=tuple(infer_caps(item["id"])))
                for item in data
                if item.get("id")
            ]
        if wire_type == "anthropic":
            headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
            response = await client.get(f"{base_url}/v1/models", headers=headers)
            response.raise_for_status()
            data = response.json().get("data", [])
            return [
                RemoteModel(external_id=item["id"], caps=tuple(infer_caps(item["id"])))
                for item in data
                if item.get("id")
            ]
    raise ValueError(f"unknown wire type '{wire_type}'")


# ---------------------------------------------------------------------------
# USER-scope row resolution
# ---------------------------------------------------------------------------


async def _resolve_target_row(
    db: AsyncSession,
    preset_key: str,
    preset: dict[str, Any],
    scope: AIScope,
    tenant_id: Any,
    user_id: Any,
) -> Optional[AIProviderModel]:
    """Find the row to reuse at the REQUESTED scope: preset-stamped first,
    then the earliest manual row with the same type + base (§15 adoption).
    Adoption never crosses scopes."""
    filters = [
        AIProviderModel.scope == scope,
        AIProviderModel.preset_key == preset_key,
    ]
    filters.append(
        AIProviderModel.tenant_id.is_(None)
        if tenant_id is None
        else AIProviderModel.tenant_id == tenant_id
    )
    filters.append(
        AIProviderModel.user_id.is_(None)
        if user_id is None
        else AIProviderModel.user_id == user_id
    )
    existing = (
        await db.execute(
            select(AIProviderModel)
            .where(*filters)
            .order_by(AIProviderModel.created_at, AIProviderModel.id)
        )
    ).scalars().first()
    if existing is not None:
        return existing
    manual_filters = [
        AIProviderModel.scope == scope,
        AIProviderModel.preset_key.is_(None),
        AIProviderModel.provider_type == preset["type"],
        AIProviderModel.api_base == preset["base_url"],
    ]
    manual_filters.append(
        AIProviderModel.tenant_id.is_(None)
        if tenant_id is None
        else AIProviderModel.tenant_id == tenant_id
    )
    manual_filters.append(
        AIProviderModel.user_id.is_(None)
        if user_id is None
        else AIProviderModel.user_id == user_id
    )
    return (
        await db.execute(
            select(AIProviderModel)
            .where(*manual_filters)
            .order_by(AIProviderModel.created_at, AIProviderModel.id)
        )
    ).scalars().first()


async def _upsert_persisted_models(
    db: AsyncSession,
    provider: AIProviderModel,
    persist_catalog: list[_PersistedModel],
) -> dict[str, AIModel]:
    """Append-union the curated catalog into the provider's model rows.

    Returns the wire-id → row map for the freshly persisted set.
    """
    existing = {
        model.model_name: model
        for model in (
            await db.execute(
                select(AIModel).where(AIModel.provider_id == provider.id)
            )
        ).scalars().all()
    }
    resolved: dict[str, AIModel] = {}
    for item in persist_catalog:
        model = existing.get(item.external_id)
        if model is None:
            model = AIModel(
                provider_id=provider.id,
                name=item.external_id,
                model_name=item.external_id,
                capabilities=list(item.caps),
                is_active=True,
                settings={},
            )
            db.add(model)
            await db.flush()
        elif not model.get_capabilities():
            model.capabilities = list(item.caps)
        item.model_id = model.id
        resolved[item.external_id] = model
    return resolved


async def _slot_alive(
    db: AsyncSession,
    scope: AIScope,
    tenant_id: Any,
    user_id: Any,
    task_type: str,
) -> tuple[Optional[AITaskAssignment], Optional[AIModel]]:
    """The slot for a task at the given scope: its highest-priority active
    row + the assigned model if that row is live (model present and
    existing)."""
    filters = [
        AITaskAssignment.scope == scope,
        AITaskAssignment.task_type == task_type,
        AITaskAssignment.is_active.is_(True),
    ]
    filters.append(
        AITaskAssignment.tenant_id.is_(None)
        if tenant_id is None
        else AITaskAssignment.tenant_id == tenant_id
    )
    filters.append(
        AITaskAssignment.user_id.is_(None)
        if user_id is None
        else AITaskAssignment.user_id == user_id
    )
    row = (
        await db.execute(
            select(AITaskAssignment)
            .where(*filters)
            .order_by(AITaskAssignment.priority.desc(), AITaskAssignment.created_at)
        )
    ).scalars().first()
    if row is None:
        return None, None
    if row.model_id is None:
        return row, None
    model = (
        await db.execute(select(AIModel).where(AIModel.id == row.model_id))
    ).scalars().first()
    if model is None:
        return row, None
    return row, model


async def _bind_slot(
    db: AsyncSession,
    scope: AIScope,
    tenant_id: Any,
    user_id: Any,
    task_type: str,
    provider: AIProviderModel,
    model: AIModel,
) -> None:
    """Bind a slot at the given scope, deactivating sibling active rows
    (mirrors the CRUD surface's one-active-assignment invariant). Never
    touches rows of another scope."""
    filters = [
        AITaskAssignment.scope == scope,
        AITaskAssignment.tenant_id == tenant_id,
        AITaskAssignment.user_id == user_id,
        AITaskAssignment.task_type == task_type,
        AITaskAssignment.is_active.is_(True),
    ]
    siblings = (
        await db.execute(
            select(AITaskAssignment)
            .where(*filters)
            .order_by(AITaskAssignment.priority.desc(), AITaskAssignment.created_at)
        )
    ).scalars().all()
    row = siblings[0] if siblings else None
    if row is None:
        row = AITaskAssignment(
            task_type=task_type,
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_id=provider.id,
            model_id=model.id,
            is_active=True,
            priority=0,
        )
        db.add(row)
    else:
        row.provider_id = provider.id
        row.model_id = model.id
        row.is_active = True
    for stale in siblings[1:]:
        stale.is_active = False
    await db.flush()


def _match_curated(model_id: str, curated: list[str]) -> Optional[str]:
    """Exact or snapshot-suffix match (``gpt-5.6-terra`` matches
    ``gpt-5.6-terra-2026-09-11``)."""
    for curated_id in curated:
        if model_id == curated_id or model_id.startswith(f"{curated_id}-"):
            return curated_id
    return None


# ---------------------------------------------------------------------------
# Setup + set-default
# ---------------------------------------------------------------------------


async def setup_provider_from_preset(
    db: AsyncSession,
    preset_key: str,
    api_key: Optional[str],
    *,
    scope: AIScope,
    tenant_id: Any,
    user_id: Any,
    name: Optional[str] = None,
    options: Optional[SetupOptions] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> SetupOutcome:
    """Run the §15 setup flow for one preset, at the requested scope
    (SYSTEM / TENANT / USER — the caller enforces role access)."""
    options = options or SetupOptions()
    preset = SETUP_PRESETS.get(preset_key)
    if preset is None:
        raise UnknownPresetError(f"unknown provider preset '{preset_key}'")

    guard_api_base(preset["base_url"])

    existing = await _resolve_target_row(
        db, preset_key, preset, scope, tenant_id, user_id
    )
    key = "" if preset["local"] else (api_key or "").strip()
    if not key and existing is not None:
        key = decrypt_secret(existing.api_key) or ""

    if existing is not None:
        existing.provider_type = preset["type"]
        existing.api_base = preset["base_url"]
        provider = existing
    else:
        provider = AIProviderModel(
            name=(name or "").strip() or preset["name"],
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_type=preset["type"],
            api_base=preset["base_url"],
            api_key=None,
            preset_key=preset_key,
            is_active=True,
            is_local=preset["local"],
            settings={},
        )
        db.add(provider)
        await db.flush()
    provider.preset_key = preset_key
    await db.flush()

    try:
        catalog = await fetch_remote_models(
            preset["wire_type"], provider.api_base, key or None, transport=transport
        )
    except Exception as error:
        classified = classify_provider_error(
            error,
            local_provider=bool(preset["local"]),
            api_key=key or None,
            attempted_preset=preset_key,
        )
        await db.rollback()
        raise SetupError(classified, str(error)[:300]) from error

    use_preset_curated = options.curated_ids is None
    curated = preset["curated_models"] if use_preset_curated else options.curated_ids
    curated = curated or []
    persist_catalog = [
        _PersistedModel(external_id=item.external_id, caps=list(item.caps))
        for item in catalog
    ]
    for item in persist_catalog:
        if not item.caps:
            item.caps = infer_caps(item.external_id)

    def curated_match(item: _PersistedModel) -> Optional[str]:
        return _match_curated(item.external_id, curated)

    any_curated_match = any(curated_match(item) is not None for item in persist_catalog)
    if curated:
        if any_curated_match:
            persist_catalog = [item for item in persist_catalog if curated_match(item)]
        elif not use_preset_curated:
            persist_catalog = []
    elif not use_preset_curated:
        persist_catalog = []
    curated_missed = bool(use_preset_curated and curated and not any_curated_match)

    by_wire_id = await _upsert_persisted_models(db, provider, persist_catalog)
    if key:
        provider.api_key = encrypt_secret(key)
    await db.flush()

    assigned: dict[str, Optional[str]] = {"chat": None, "vision": None, "stt": None}

    preferred = preset["preferred_model"]
    preferred_model_id = preferred["id"] if preferred else None
    preferred_resolved_id = None
    if preferred_model_id:
        preferred_resolved_id = next(
            (
                item.external_id
                for item in persist_catalog
                if item.external_id == preferred_model_id
                or _match_curated(item.external_id, curated) == preferred_model_id
            ),
            None,
        )
    fallback_candidate_id = next(
        (
            item.external_id
            for curated_id in curated
            for item in persist_catalog
            if _match_curated(item.external_id, curated) == curated_id
        ),
        None,
    )
    assignment_candidate_id = preferred_resolved_id or fallback_candidate_id
    if preferred_resolved_id:
        assignment_vision_capable = bool(
            preferred and "vision" in (preferred.get("caps") or [])
        )
    elif assignment_candidate_id:
        assignment_vision_capable = "vision" in by_wire_id[assignment_candidate_id].get_capabilities()
    else:
        assignment_vision_capable = False

    _, chat_model = await _slot_alive(
        db, scope, tenant_id, user_id, _SLOT_TASK_TYPES["chat"]
    )
    vision_candidate_id: Optional[str] = None
    vision_capable_flag = False
    chat_wire_id = (
        chat_model.model_name
        if chat_model is not None and chat_model.model_name in by_wire_id
        else None
    )
    if chat_wire_id and "vision" in by_wire_id[chat_wire_id].get_capabilities():
        vision_candidate_id = chat_wire_id
        vision_capable_flag = True
    if vision_candidate_id is None and assignment_candidate_id:
        vision_candidate_id = assignment_candidate_id
        vision_capable_flag = assignment_vision_capable

    if options.bind_chat and assignment_candidate_id and chat_model is None:
        assigned["chat"] = assignment_candidate_id
        await _bind_slot(
            db,
            scope,
            tenant_id,
            user_id,
            _SLOT_TASK_TYPES["chat"],
            provider,
            by_wire_id[assignment_candidate_id],
        )
    if options.bind_vision and vision_candidate_id and vision_capable_flag:
        _, vision_model = await _slot_alive(
            db, scope, tenant_id, user_id, _SLOT_TASK_TYPES["vision"]
        )
        if vision_model is None:
            assigned["vision"] = vision_candidate_id
            await _bind_slot(
                db,
                scope,
                tenant_id,
                user_id,
                _SLOT_TASK_TYPES["vision"],
                provider,
                by_wire_id[vision_candidate_id],
            )

    stt_model_id = preset["stt_model"]
    stt_resolved_id = None
    if stt_model_id:
        stt_resolved_id = next(
            (
                item.external_id
                for item in persist_catalog
                if item.external_id == stt_model_id
                or _match_curated(item.external_id, curated) == stt_model_id
            ),
            None,
        )
    if options.bind_stt and stt_resolved_id:
        _, stt_model = await _slot_alive(
            db, scope, tenant_id, user_id, _SLOT_TASK_TYPES["stt"]
        )
        if stt_model is None:
            assigned["stt"] = stt_resolved_id
            await _bind_slot(
                db,
                scope,
                tenant_id,
                user_id,
                _SLOT_TASK_TYPES["stt"],
                provider,
                by_wire_id[stt_resolved_id],
            )

    await db.commit()

    return SetupOutcome(
        provider_id=provider.id,
        catalog_count=len(persist_catalog),
        curated_missed=curated_missed,
        assigned_chat_model=assigned["chat"],
        assigned_vision_model=assigned["vision"],
        assigned_stt_model=assigned["stt"],
    )


async def set_default_model(
    db: AsyncSession,
    provider: AIProviderModel,
    model_name: str,
    task: str = "default",
) -> AIModel:
    """Bind one of the provider's models to a task slot at the PROVIDER's
    own scope (the endpoint enforces role access via ``check_scope_access``).

    Capability-guarded (a model must advertise the task's required
    capability); unknown model ids are created as manual rows; ids that
    already exist under a DIFFERENT provider are rejected.
    """
    if TaskType.from_string(task) is None:
        raise ValueError(f"unknown task '{task}'")
    model_name = model_name.strip()
    if not model_name:
        raise ValueError("model_name is required")

    model = (
        await db.execute(
            select(AIModel).where(
                AIModel.provider_id == provider.id, AIModel.model_name == model_name
            )
        )
    ).scalars().first()
    if model is None:
        elsewhere = (
            await db.execute(
                select(AIModel).where(AIModel.model_name == model_name)
            )
        ).scalars().first()
        if elsewhere is not None:
            raise CrossProviderModelError(
                f"model '{model_name}' belongs to another provider"
            )
        model = AIModel(
            provider_id=provider.id,
            name=model_name,
            model_name=model_name,
            capabilities=infer_caps(model_name),
            is_active=True,
            settings={},
        )
        db.add(model)
        await db.flush()

    required = required_capabilities_for_task(task)
    have = set(model.get_capabilities())
    if required and not any(c in have for c in (r.value for r in required)):
        raise ValueError(f"task requires the '{sorted(r.value for r in required)[0]}' capability")

    await _bind_slot(
        db,
        provider.scope,
        provider.tenant_id,
        provider.user_id,
        task,
        provider,
        model,
    )
    await db.commit()
    return model

"""Tenant-scoped model price overrides and call-cost views (plan 58 P2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_pricing import resolve_price
from app.ai.model_pricing_data import MODEL_PRICING_COMPILED, MODEL_PRICING_ROWS
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.ai_provider_model import AIModel, AIProviderModel
from app.models.ai_task_model import AICallLog, AIPriceOverride
from app.models.enums import Role
from app.schemas.user import TokenData

router = APIRouter(prefix="/ai", tags=["AI Pricing"])


class PriceOverrideIn(BaseModel):
    tenant_id: UUID | None = None
    provider_key: str | None = Field(default=None, max_length=40)
    provider_ref: UUID | None = None
    model: str = Field(min_length=1, max_length=200)
    match: Literal["exact", "prefix"] = "exact"
    input_micros_per_million: int = Field(ge=0)
    output_micros_per_million: int = Field(ge=0)
    metering: Literal["tokens", "input_only", "output_only"] = "tokens"


class PriceOverrideOut(PriceOverrideIn):
    id: str
    updated_at: str


class EffectivePriceOut(BaseModel):
    provider: str | None
    provider_id: str | None
    model: str
    source: str
    input_micros_per_million: int
    output_micros_per_million: int
    metering: str
    reviewed_at: str | None = None
    price_version: str | None = None


class PricesOut(BaseModel):
    tenant_id: str
    overrides: list[PriceOverrideOut]
    effective: list[EffectivePriceOut]


class CostRow(BaseModel):
    provider: str
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_micros: int | None
    priced_calls: int
    unpriced_calls: int


class CostsOut(BaseModel):
    window_days: int
    since: datetime
    rows: list[CostRow]


def _is_local(base_url: str | None) -> bool:
    host = (urlparse(base_url or "").hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")


def _require_admin(user: TokenData) -> None:
    if user.role not in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
        raise HTTPException(status_code=403, detail="Admin role required")


def _target_tenant(user: TokenData, requested: UUID | None) -> UUID:
    _require_admin(user)
    target = requested or user.tenant_id
    if target is None:
        raise HTTPException(status_code=422, detail="tenant_id is required")
    if user.role != Role.SYSTEM_ADMIN.value and str(target) != str(user.tenant_id):
        raise HTTPException(status_code=403, detail="Cannot manage another tenant's prices")
    return target


def _override_out(row: AIPriceOverride) -> PriceOverrideOut:
    return PriceOverrideOut(
        id=str(row.id),
        tenant_id=row.tenant_id,
        provider_key=row.provider_key,
        provider_ref=row.provider_ref,
        model=row.model,
        match=row.match,
        input_micros_per_million=int(row.input_micros_per_million),
        output_micros_per_million=int(row.output_micros_per_million),
        metering=row.metering,
        updated_at=row.updated_at.isoformat(),
    )


def _override_dicts(rows: list[AIPriceOverride]) -> list[dict[str, Any]]:
    return [
        {
            "providerRef": str(row.provider_ref) if row.provider_ref else None,
            "provider": row.provider_key,
            "model": row.model,
            "match": row.match,
            "inputMicrosPerMillion": int(row.input_micros_per_million),
            "outputMicrosPerMillion": int(row.output_micros_per_million),
            "metering": row.metering,
            "id": str(row.id),
            "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


@router.get("/prices", response_model=PricesOut)
async def list_prices(
    tenant_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> PricesOut:
    target = _target_tenant(current_user, tenant_id)
    override_rows = list(
        (
            await db.execute(select(AIPriceOverride).where(AIPriceOverride.tenant_id == target))
        ).scalars()
    )
    override_dicts = _override_dicts(override_rows)
    effective: list[EffectivePriceOut] = []
    rows = (
        await db.execute(
            select(AIModel, AIProviderModel)
            .join(AIProviderModel, AIModel.provider_id == AIProviderModel.id)
            .where(AIModel.is_active.is_(True), AIProviderModel.is_active.is_(True))
        )
    ).all()
    for model, provider in rows:
        is_local = (
            bool(model.is_local)
            if model.is_local is not None
            else bool(provider.is_local) or _is_local(provider.api_base)
        )
        resolution = resolve_price(
            rows=MODEL_PRICING_ROWS,
            compiled=MODEL_PRICING_COMPILED,
            model=model.model_name,
            provider_key=provider.preset_key,
            provider_row_id=str(provider.id),
            is_local=is_local,
            user_rows=override_dicts,
        )
        effective.append(
            EffectivePriceOut(
                provider=provider.preset_key or provider.name,
                provider_id=str(provider.id),
                model=model.model_name,
                source=resolution.source if resolution else "unknown",
                input_micros_per_million=(resolution.input_micros_per_million if resolution else 0),
                output_micros_per_million=(
                    resolution.output_micros_per_million if resolution else 0
                ),
                metering=resolution.metering if resolution else "tokens",
                reviewed_at=resolution.reviewed_at if resolution else None,
                price_version=resolution.price_version if resolution else None,
            )
        )
    return PricesOut(
        tenant_id=str(target),
        overrides=[_override_out(row) for row in override_rows],
        effective=effective,
    )


@router.put("/prices", response_model=PriceOverrideOut)
async def put_price(
    body: PriceOverrideIn,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> PriceOverrideOut:
    target = _target_tenant(current_user, body.tenant_id)
    if body.provider_key and body.provider_ref:
        raise HTTPException(status_code=422, detail="bind provider_key or provider_ref, not both")
    if body.provider_ref is not None:
        provider = await db.get(AIProviderModel, body.provider_ref)
        if provider is None:
            raise HTTPException(status_code=422, detail="unknown provider_ref")
    query = select(AIPriceOverride).where(
        AIPriceOverride.tenant_id == target,
        AIPriceOverride.model == body.model,
        AIPriceOverride.match == body.match,
    )
    query = query.where(
        AIPriceOverride.provider_key.is_(None)
        if body.provider_key is None
        else AIPriceOverride.provider_key == body.provider_key
    )
    query = query.where(
        AIPriceOverride.provider_ref.is_(None)
        if body.provider_ref is None
        else AIPriceOverride.provider_ref == body.provider_ref
    )
    row = (await db.execute(query)).scalars().first()
    if row is None:
        row = AIPriceOverride(
            tenant_id=target,
            provider_key=body.provider_key,
            provider_ref=body.provider_ref,
            model=body.model,
            match=body.match,
            input_micros_per_million=body.input_micros_per_million,
            output_micros_per_million=body.output_micros_per_million,
            metering=body.metering,
        )
        db.add(row)
    else:
        row.input_micros_per_million = body.input_micros_per_million
        row.output_micros_per_million = body.output_micros_per_million
        row.metering = body.metering
        row.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return _override_out(row)


@router.delete("/prices/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price(
    override_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> None:
    target = _target_tenant(current_user, None)
    row = await db.get(AIPriceOverride, override_id)
    if row is None or row.tenant_id != target:
        raise HTTPException(status_code=404, detail="price override not found")
    await db.delete(row)
    await db.commit()


@router.get("/usage/costs", response_model=CostsOut)
async def usage_costs(
    window_days: int = 30,
    tenant_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> CostsOut:
    target = _target_tenant(current_user, tenant_id)
    window_days = max(1, min(window_days, 365))
    since = datetime.now(UTC) - timedelta(days=window_days)
    calls = func.count(AICallLog.id)
    priced = func.count(AICallLog.id).filter(AICallLog.cost_micros.is_not(None))
    provider_name = func.coalesce(AIProviderModel.name, "unknown")
    model_name = func.coalesce(AIModel.model_name, AICallLog.provider_key, "unknown")
    rows = (
        await db.execute(
            select(
                provider_name,
                model_name,
                calls,
                func.coalesce(func.sum(AICallLog.prompt_tokens), 0),
                func.coalesce(func.sum(AICallLog.completion_tokens), 0),
                func.sum(AICallLog.cost_micros),
                priced,
            )
            .outerjoin(AIProviderModel, AIProviderModel.id == AICallLog.provider_id)
            .outerjoin(AIModel, AIModel.id == AICallLog.model_id)
            .where(AICallLog.tenant_id == target, AICallLog.started_at >= since)
            .group_by(
                AIProviderModel.name,
                AIModel.model_name,
                AICallLog.provider_key,
            )
            .order_by(calls.desc())
        )
    ).all()
    return CostsOut(
        window_days=window_days,
        since=since,
        rows=[
            CostRow(
                provider=str(provider or "unknown"),
                model=str(model or "unknown"),
                calls=int(count or 0),
                prompt_tokens=int(prompt or 0),
                completion_tokens=int(completion or 0),
                cost_micros=int(cost) if cost is not None else None,
                priced_calls=int(priced_calls or 0),
                unpriced_calls=int(count or 0) - int(priced_calls or 0),
            )
            for provider, model, count, prompt, completion, cost, priced_calls in rows
        ],
    )

"""The ONE resolution precedence matrix + the DB-level assignment law (plan 4 W2).

Precedence (ADR-0036 / ADR-0037), implemented once in
``app.ai.providers.resolution`` and pinned here:

    specialist primary → specialist fallback → capability default → unavailable

plus the honest-state rule: an EMPTY specialist slot inherits the capability
default, but a configured-yet-unusable slot reports unavailable instead of being
silently replaced.

The law itself is tested at the DATABASE layer (IntegrityError, not HTTP 422):
one ACTIVE row per ``(tenant_id, user_id, task_type_id)`` with
``NULLS NOT DISTINCT`` — which is precisely what makes system/tenant scope
(NULL ``user_id``) unique — and revoked rows are invisible to resolution while
their slots become reusable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.ai.providers.resolution import ResolutionHop, assignment_candidates, resolve_task
from app.ai.providers.task_defs import task_def_id
from app.core.database import AsyncSessionLocal
from app.models.ai_provider_model import AIModel, AIProviderModel, AIScope, AITaskAssignment
from app.models.tenant_model import TenantModel


@pytest_asyncio.fixture
async def res_ctx():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tenant_id, name="Resolution Tenant", slug=f"res-{tenant_id}"))
        await session.commit()
    ctx = {"tenant_id": tenant_id, "user_id": user_id, "assignment_ids": [], "provider_ids": []}
    # Resolution semantics are global (system-scope rows resolve across
    # tenants), and the suite truncates once per SESSION — so an assignment
    # left behind by an earlier test would win over this fixture's own and
    # make these assertions order-dependent. Establish a known baseline.
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AITaskAssignment))
        await session.commit()
    yield ctx
    async with AsyncSessionLocal() as session:
        if ctx["assignment_ids"]:
            await session.execute(
                delete(AITaskAssignment).where(AITaskAssignment.id.in_(ctx["assignment_ids"]))
            )
        if ctx["provider_ids"]:
            await session.execute(
                delete(AIProviderModel).where(AIProviderModel.id.in_(ctx["provider_ids"]))
            )
        await session.commit()


async def seed_pair(ctx, *, tag: str, scope: AIScope = AIScope.USER) -> tuple[Any, Any]:
    """A provider+model pair at ``scope`` (model/adapter rows, not config)."""
    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name=f"res-{tag}",
            scope=scope,
            tenant_id=ctx["tenant_id"] if scope != AIScope.SYSTEM else None,
            user_id=ctx["user_id"] if scope == AIScope.USER else None,
            provider_type="openai",
            api_base="https://api.openai.com/v1",
            api_key=None,
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.flush()
        model = AIModel(
            provider_id=provider.id,
            name=f"res-{tag}",
            model_name=f"res-{ctx['tenant_id']}-{tag}",
            capabilities=["text"],
            is_active=True,
            settings={},
        )
        session.add(model)
        await session.commit()
        await session.refresh(provider)
        await session.refresh(model)
        ctx["provider_ids"].append(provider.id)
        return provider, model


async def bind(
    ctx,
    provider,
    model,
    task_id: str,
    *,
    scope: AIScope = AIScope.USER,
    fallback_model_id=None,
    fallback_provider_id=None,
    revoked_at=None,
) -> AITaskAssignment:
    async with AsyncSessionLocal() as session:
        row = AITaskAssignment(
            task_type_id=task_def_id(task_id),
            scope=scope,
            tenant_id=ctx["tenant_id"] if scope != AIScope.SYSTEM else None,
            user_id=ctx["user_id"] if scope == AIScope.USER else None,
            provider_id=provider.id if provider is not None else None,
            model_id=model.id if model is not None else None,
            fallback_provider_id=fallback_provider_id,
            fallback_model_id=fallback_model_id,
            revoked_at=revoked_at,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        ctx["assignment_ids"].append(row.id)
        return row


# ---------------------------------------------------------------------------
# precedence
# ---------------------------------------------------------------------------


async def test_specialist_primary_wins(res_ctx):
    provider, model = await seed_pair(res_ctx, tag="primary")
    await bind(res_ctx, provider, model, "define_biomarker")

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(
            db, "define_biomarker", res_ctx["tenant_id"], res_ctx["user_id"]
        )
    assert resolved is not None
    assert resolved.hop is ResolutionHop.PRIMARY
    assert resolved.task_id == "define_biomarker"
    assert resolved.model.id == model.id


async def test_empty_specialist_inherits_the_capability_default(res_ctx):
    default_provider, default_model = await seed_pair(res_ctx, tag="default")
    await bind(res_ctx, default_provider, default_model, "default")

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(
            db, "define_anatomy_graph", res_ctx["tenant_id"], res_ctx["user_id"]
        )
    assert resolved is not None
    assert resolved.hop is ResolutionHop.DEFAULT
    assert resolved.task_id == "default"
    assert resolved.requested_task_id == "define_anatomy_graph"
    assert resolved.model.id == default_model.id


async def test_specialist_fallback_beats_the_capability_default(res_ctx):
    default_provider, default_model = await seed_pair(res_ctx, tag="default")
    primary_provider, primary_model = await seed_pair(res_ctx, tag="primary")
    fallback_provider, fallback_model = await seed_pair(res_ctx, tag="fallback")
    await bind(res_ctx, default_provider, default_model, "default")
    await bind(
        res_ctx,
        primary_provider,
        primary_model,
        "define_biomarker",
        fallback_provider_id=fallback_provider.id,
        fallback_model_id=fallback_model.id,
    )

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(
            db,
            "define_biomarker",
            res_ctx["tenant_id"],
            res_ctx["user_id"],
            # The primary is usable for a plain task, so the matrix needs a
            # predicate that rejects it to exercise the fallback rung.
            accept=lambda candidate: (
                None if candidate.hop is ResolutionHop.FALLBACK else "primary rejected"
            ),
        )
    assert resolved is not None
    assert resolved.hop is ResolutionHop.FALLBACK
    assert resolved.model.id == fallback_model.id
    assert resolved.assignment.fallback_model_id == fallback_model.id


async def test_configured_but_unusable_specialist_does_not_inherit(res_ctx):
    """A configured-yet-broken slot is reported, not silently replaced."""
    default_provider, default_model = await seed_pair(res_ctx, tag="default")
    _broken_provider, _broken_model = await seed_pair(res_ctx, tag="broken")
    await bind(res_ctx, default_provider, default_model, "default")
    # A specialist row with no provider at all — unusable.
    await bind(res_ctx, None, None, "define_biomarker")

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(
            db, "define_biomarker", res_ctx["tenant_id"], res_ctx["user_id"]
        )
    assert resolved is None


async def test_capability_default_ignores_its_own_fallback_columns(res_ctx):
    """ADR-0037: a default IS the fallback — no second hop of its own."""
    _provider, _model = await seed_pair(res_ctx, tag="default")
    other_provider, other_model = await seed_pair(res_ctx, tag="other")
    await bind(
        res_ctx,
        None,
        None,
        "default",
        fallback_provider_id=other_provider.id,
        fallback_model_id=other_model.id,
    )

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(db, "default", res_ctx["tenant_id"], res_ctx["user_id"])
    assert resolved is None, "a capability default must not use a fallback hop"


async def test_scope_cascade_prefers_the_most_specific_row(res_ctx):
    system_provider, system_model = await seed_pair(res_ctx, tag="system", scope=AIScope.SYSTEM)
    tenant_provider, tenant_model = await seed_pair(res_ctx, tag="tenant", scope=AIScope.TENANT)
    user_provider, user_model = await seed_pair(res_ctx, tag="user")
    await bind(res_ctx, system_provider, system_model, "chat", scope=AIScope.SYSTEM)
    await bind(res_ctx, tenant_provider, tenant_model, "chat", scope=AIScope.TENANT)
    await bind(res_ctx, user_provider, user_model, "chat", scope=AIScope.USER)

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(db, "chat", res_ctx["tenant_id"], res_ctx["user_id"])
        rows = await assignment_candidates(db, "chat", res_ctx["tenant_id"], res_ctx["user_id"])

    assert resolved is not None and resolved.model.id == user_model.id
    assert [row.scope for row in rows] == [AIScope.USER, AIScope.TENANT, AIScope.SYSTEM]


async def test_revoked_rows_are_invisible(res_ctx):
    provider, model = await seed_pair(res_ctx, tag="revoked")
    await bind(
        res_ctx,
        provider,
        model,
        "chat",
        revoked_at=datetime.now(UTC),
    )

    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(db, "chat", res_ctx["tenant_id"], res_ctx["user_id"])
        rows = await assignment_candidates(db, "chat", res_ctx["tenant_id"], res_ctx["user_id"])
    assert resolved is None
    assert rows == []


async def test_unknown_task_is_unavailable(res_ctx):
    async with AsyncSessionLocal() as db:
        resolved = await resolve_task(db, "not_a_task", res_ctx["tenant_id"], res_ctx["user_id"])
    assert resolved is None


# ---------------------------------------------------------------------------
# the DB law (IntegrityError, not an app-side check)
# ---------------------------------------------------------------------------


async def test_database_refuses_a_second_active_row_in_one_scope(res_ctx):
    provider, model = await seed_pair(res_ctx, tag="dup")
    await bind(res_ctx, provider, model, "chat")

    with pytest.raises(IntegrityError):
        await bind(res_ctx, provider, model, "chat")


async def test_database_refuses_a_second_active_system_row(res_ctx):
    """NULLS NOT DISTINCT: system rows are (NULL, NULL, task) — still unique."""
    provider, model = await seed_pair(res_ctx, tag="sys", scope=AIScope.SYSTEM)
    await bind(res_ctx, provider, model, "chat", scope=AIScope.SYSTEM)

    with pytest.raises(IntegrityError):
        await bind(res_ctx, provider, model, "chat", scope=AIScope.SYSTEM)


async def test_database_refuses_a_second_active_tenant_row(res_ctx):
    """NULLS NOT DISTINCT: tenant rows share ``user_id IS NULL``."""
    provider, model = await seed_pair(res_ctx, tag="ten", scope=AIScope.TENANT)
    await bind(res_ctx, provider, model, "chat", scope=AIScope.TENANT)

    with pytest.raises(IntegrityError):
        await bind(res_ctx, provider, model, "chat", scope=AIScope.TENANT)


async def test_scope_rows_coexist_and_a_revoked_slot_is_reusable(res_ctx):
    provider, model = await seed_pair(res_ctx, tag="coexist")
    _, other_model = await seed_pair(res_ctx, tag="coexist-2")
    await bind(res_ctx, provider, model, "chat", scope=AIScope.SYSTEM)
    await bind(res_ctx, provider, model, "chat", scope=AIScope.TENANT)
    first = await bind(res_ctx, provider, other_model, "chat", scope=AIScope.USER)

    async with AsyncSessionLocal() as session:
        await session.execute(
            AITaskAssignment.__table__.update()
            .where(AITaskAssignment.id == first.id)
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()

    replacement = await bind(res_ctx, provider, model, "chat", scope=AIScope.USER)
    assert replacement.id != first.id

"""Chat branching — the family contract in health's backend (plan 13 H2).

Gate bundle (plan 13 §2 Phase H):
  * tree projection + variant decoration round-trips (walk, select, re-walk)
  * edit/regenerate row semantics (siblings, pointer flips, kept subtrees)
  * migration backfill: legacy rows become identical linear trees
  * per-session single-flight turn lock (second concurrent turn on ANY
    branch is rejected while one streams) — see test_chat_turn_lock.py
  * interrupt-survives-branch + edit during an interrupted turn — see
    test_chat_branching_threads.py
"""

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text

from alembic import command
from app.core.config import settings
from app.models.enums import Role
from app.services.chat_branching import (
    branch_thread_key,
    regenerate_thread_key,
    walk_active_path,
)
from app.services.chat_session_service import ChatSessionService
from tests._auth_helpers import auth_headers, create_tenant, create_user

PRE_BRANCH_HEAD = "h5m2f3a4t6o7"
BRANCH_HEAD = "c1h2a3t4b5r6"


async def _seed_turn(svc: ChatSessionService, session_id, user_id, tenant_id, question, answer):
    """Persist one user+assistant exchange at the active tip."""
    user_msg = await svc.save_message(
        session_id=session_id,
        role="user",
        content={"text": question},
        owner_user_id=user_id,
    )
    assistant_msg = await svc.save_message(
        session_id=session_id,
        role="assistant",
        content={"text": answer},
    )
    return user_msg, assistant_msg


async def _setup():
    tenant_id = await create_tenant()
    user = await create_user(role=Role.USER, tenant_id=tenant_id)
    headers = await auth_headers(user)
    return tenant_id, user, headers


async def test_tree_projection_and_variant_decoration_round_trip():
    """Gate (d): walk -> select -> re-walk + decoration survives."""
    from app.core.database import AsyncSessionLocal

    tenant_id, user, _headers = await _setup()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)

        u1, a1 = await _seed_turn(svc, session.id, user.id, tenant_id, "Q1", "A1")
        u2, a2 = await _seed_turn(svc, session.id, user.id, tenant_id, "Q2", "A2")

        visible = await svc.get_active_path_decorated(session.id, user.id, tenant_id)
        assert [m.id for m in visible] == [u1.id, a1.id, u2.id, a2.id]
        for m in visible:
            assert m.variant_count == 1
            assert m.variant_index == 1
            assert m.sibling_ids == [m.id]

        # Edit Q2 -> variant: new sibling under a1, a1's pointer flips. The
        # old subtree (u2 -> A2) goes off-path — the edited branch has no
        # reply yet (its turn streams next).
        _session2, edited = await svc.edit_message(u2.id, user.id, tenant_id, {"text": "Q2 edited"})
        visible = await svc.get_active_path_decorated(session.id, user.id, tenant_id)
        assert [m.content["text"] for m in visible] == ["Q1", "A1", "Q2 edited"]
        assert visible[2].id == edited.id
        assert visible[2].parent_id == a1.id
        # Variant decoration: Q2-level now has two siblings.
        assert visible[2].variant_count == 2
        assert visible[2].variant_index == 2
        assert set(visible[2].sibling_ids) == {u2.id, edited.id}

        # Tree projection: every node present, children linked, active pointers.
        tree = await svc.tree(session.id, user.id, tenant_id)
        assert tree is not None
        nodes = {n["id"]: n for n in tree["nodes"]}
        assert tree["active_root_id"] == u1.id
        assert nodes[u1.id]["children"] == [a1.id]
        assert nodes[a1.id]["children"] == [u2.id, edited.id]
        assert nodes[a1.id]["active_child_id"] == edited.id
        assert nodes[edited.id]["excerpt"] == "Q2 edited"

        # Select the ORIGINAL Q2 -> level-flip exactly one pointer.
        rows = await svc.select_message(u2.id, user.id, tenant_id)
        assert [m.content["text"] for m in rows] == ["Q1", "A1", "Q2", "A2"]
        assert rows[2].variant_index == 1

        # Re-walk: pointer state persisted (a1 active child back to u2).
        async with AsyncSessionLocal() as db2:
            svc2 = ChatSessionService(db2)
            visible2 = await svc2.get_active_path_decorated(session.id, user.id, tenant_id)
            assert [m.id for m in visible2] == [u1.id, a1.id, u2.id, a2.id]
            tree2 = await svc2.tree(session.id, user.id, tenant_id)
            assert tree2 is not None
            assert {n["id"]: n for n in tree2["nodes"]}[a1.id]["active_child_id"] == u2.id


async def test_edit_and_regenerate_row_semantics():
    """Edit keeps the old subtree as a variant; regenerate re-rolls under the
    target user message with the previous reply kept switchable."""
    from app.core.database import AsyncSessionLocal

    tenant_id, user, _headers = await _setup()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)
        u1, a1 = await _seed_turn(svc, session.id, user.id, tenant_id, "Q1", "A1")

        # Regenerate preparation: path activation only, no rows written.
        _s, target = await svc.prepare_regenerate(u1.id, user.id, tenant_id)
        assert target.id == u1.id

        # The regenerated reply persists under the target explicitly (the
        # turn's turn_parent_id) — sibling of a1, flips u1's pointer.
        a1_new = await svc.save_message(
            session_id=session.id,
            role="assistant",
            content={"text": "A1 regenerated"},
            parent_id=u1.id,
        )
        visible = await svc.get_active_path_messages(session.id, user.id, tenant_id)
        assert [m.id for m in visible] == [u1.id, a1_new.id]
        # The old reply is kept as a switchable variant.
        rows = await svc.select_message(a1.id, user.id, tenant_id)
        assert [m.id for m in rows] == [u1.id, a1.id]

        # Editing an assistant message is rejected (family contract).
        with pytest.raises(ValueError, match="Only user messages"):
            await svc.edit_message(a1.id, user.id, tenant_id, {"text": "nope"})
        with pytest.raises(ValueError, match="Regenerate targets a user message"):
            await svc.prepare_regenerate(a1_new.id, user.id, tenant_id)

        # Foreign user / tenant: hidden.
        stranger_tenant = await create_tenant()
        stranger = await create_user(role=Role.USER, tenant_id=stranger_tenant)
        with pytest.raises(LookupError):
            await svc.edit_message(u1.id, stranger.id, tenant_id, {"text": "x"})
        with pytest.raises(LookupError):
            await svc.edit_message(u1.id, user.id, stranger_tenant, {"text": "x"})


async def test_edit_inherits_original_images():
    from app.core.database import AsyncSessionLocal

    tenant_id, user, _headers = await _setup()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)
        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "scan?", "images": ["data:image/png;base64,AAA"]},
            owner_user_id=user.id,
        )
        _session2, edited = await svc.edit_message(u1.id, user.id, tenant_id, {"text": "scan 2?"})
        assert edited.content["text"] == "scan 2?"
        assert edited.content["images"] == ["data:image/png;base64,AAA"]


async def test_thread_keys_follow_divergence():
    """The plan 11 §6 health ruling: the legacy thread stays the bare
    session id on the un-branched spine; edits and their descendants run on
    per-branch threads; regenerated variants key the thread on the re-roll
    anchor."""
    from app.core.database import AsyncSessionLocal

    tenant_id, user, _headers = await _setup()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)
        u1, a1 = await _seed_turn(svc, session.id, user.id, tenant_id, "Q1", "A1")

        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert branch_thread_key(session.id, all_messages, u1) == str(session.id)

        _session2, edited = await svc.edit_message(u1.id, user.id, tenant_id, {"text": "Q1'"})
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        branch_key = f"{session.id}:{edited.id}"
        assert branch_thread_key(session.id, all_messages, edited) == branch_key

        # Descendants of the edit branch keep the SAME branch key…
        a_branch = await svc.save_message(
            session_id=session.id, role="assistant", content={"text": "A'"}
        )
        u_next = await svc.save_message(session_id=session.id, role="user", content={"text": "Q2"})
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert branch_thread_key(session.id, all_messages, a_branch) == branch_key
        assert branch_thread_key(session.id, all_messages, u_next) == branch_key

        # …and the original spine keeps the legacy key after the branch.
        assert branch_thread_key(session.id, all_messages, u1) == str(session.id)

        # Regenerate re-roll on a BRANCHED lineage rejoins the branch thread
        # (the edit marker dominates; re-rolls supersede within a branch).
        assert regenerate_thread_key(session.id, all_messages, u_next) == branch_key

        # Card derivation (resume side) mirrors the turn side for both shapes.
        from app.services.chat_branching import card_thread_key

        # A FIRST reply under u_next is a normal send on this branch — its
        # card keys on the branch divergence (the edit), like its turn did.
        a_first = await svc.save_message(
            session_id=session.id, role="assistant", content={"text": "A2"}, parent_id=u_next.id
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert card_thread_key(session.id, all_messages, a_first) == branch_key
        # A SECOND sibling under u_next is a regenerated variant — its card
        # keys on the re-roll anchor, exactly where that turn ran.
        a_variant = await svc.save_message(
            session_id=session.id, role="assistant", content={"text": "A2v"}, parent_id=u_next.id
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert card_thread_key(session.id, all_messages, a_variant) == branch_thread_key(
            session.id, all_messages, u_next
        )
        assert regenerate_thread_key(session.id, all_messages, u_next) == branch_thread_key(
            session.id, all_messages, u_next
        )
        assert card_thread_key(session.id, all_messages, a_branch) == branch_key
        first_reply = next(m for m in all_messages if m.id == a1.id)
        assert card_thread_key(session.id, all_messages, first_reply) == str(session.id)


async def test_walk_survives_dangling_pointers():
    """Service-enforced pointers: a walk with a stale pointer falls back to
    the newest sibling instead of breaking (cycle/dangling safe)."""
    from app.core.database import AsyncSessionLocal

    tenant_id, user, _headers = await _setup()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)
        u1, a1 = await _seed_turn(svc, session.id, user.id, tenant_id, "Q1", "A1")

        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        session_row = await svc.get_owned_session(session.id, user.id, tenant_id)
        assert session_row is not None
        session_row.active_root_id = uuid_mod.uuid4()  # dangling
        u1.active_child_id = uuid_mod.uuid4()  # dangling
        await db.commit()

        path = walk_active_path(all_messages, session_row.active_root_id)
        assert [m.id for m in path] == [u1.id, a1.id]


async def test_migration_backfills_linear_trees():
    """Legacy-data test (family-migration rule): rows seeded on the
    pre-branch revision upgrade into identical linear trees."""
    if not settings.POSTGRES_DB.endswith("_test"):
        pytest.skip("legacy-shape seeding only runs against the *_test database")

    tenant_id = await create_tenant()
    user = await create_user(role=Role.USER, tenant_id=tenant_id)

    sync_url = (
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    cfg = AlembicConfig("alembic.ini")
    engine = create_engine(sync_url)
    session_id = uuid_mod.uuid4()
    other_session_id = uuid_mod.uuid4()
    base = datetime.now(UTC)
    ids = []
    try:
        command.downgrade(cfg, PRE_BRANCH_HEAD)
        with engine.begin() as conn:
            for sid in (session_id, other_session_id):
                conn.execute(
                    text(
                        "INSERT INTO chat_sessions (id, tenant_id, user_id, title)"
                        " VALUES (:id, :tid, :uid, 'legacy')"
                    ),
                    {"id": sid, "tid": tenant_id, "uid": user.id},
                )
            for i in range(3):
                mid = uuid_mod.uuid4()
                ids.append(mid)
                conn.execute(
                    text(
                        "INSERT INTO chat_messages (id, session_id, role, content, created_at)"
                        " VALUES (:id, :sid, 'user', '{\"text\": \"m\"}', :ts)"
                    ),
                    {
                        "id": mid,
                        "sid": session_id,
                        "ts": base + timedelta(seconds=i),
                    },
                )
        command.upgrade(cfg, BRANCH_HEAD)
        with engine.begin() as conn:
            root = conn.execute(
                text("SELECT active_root_id FROM chat_sessions WHERE id = :sid"),
                {"sid": session_id},
            ).scalar_one()
            chain = conn.execute(
                text(
                    "SELECT id, parent_id, active_child_id FROM chat_messages"
                    " WHERE session_id = :sid ORDER BY created_at"
                ),
                {"sid": session_id},
            ).fetchall()
            other_root = conn.execute(
                text("SELECT active_root_id FROM chat_sessions WHERE id = :sid"),
                {"sid": other_session_id},
            ).scalar_one()
        assert root == ids[0]
        assert other_root is None  # empty session stays empty
        assert [(row.id, row.parent_id, row.active_child_id) for row in chain] == [
            (ids[0], None, ids[1]),
            (ids[1], ids[0], ids[2]),
            (ids[2], ids[1], None),
        ]
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()


async def test_branching_endpoints_select_and_tree(async_client):
    tenant_id, user, headers = await _setup()
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)
        u1, a1 = await _seed_turn(svc, session.id, user.id, tenant_id, "Q1", "A1")
        _s, edited = await svc.edit_message(u1.id, user.id, tenant_id, {"text": "Q1 edited"})

    resp = await async_client.get(
        f"/api/v1/ai-assistance/sessions/{session.id}/tree", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_root_id"] == str(edited.id)
    assert {n["id"] for n in body["nodes"]} == {str(u1.id), str(edited.id), str(a1.id)}

    resp = await async_client.post(
        f"/api/v1/ai-assistance/messages/{u1.id}/select", headers=headers
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["content"]["text"] for r in rows] == ["Q1", "A1"]
    assert rows[0]["variant_count"] == 2
    assert rows[0]["parent_id"] is None
    assert set(rows[0]["sibling_ids"]) == {str(u1.id), str(edited.id)}

    # Hidden 404 for a stranger, 400 for regenerating an assistant message.
    stranger_headers = await auth_headers(await create_user(role=Role.USER, tenant_id=tenant_id))
    resp = await async_client.post(
        f"/api/v1/ai-assistance/messages/{u1.id}/select", headers=stranger_headers
    )
    assert resp.status_code == 404
    resp = await async_client.post(
        f"/api/v1/ai-assistance/messages/{a1.id}/regenerate", headers=headers
    )
    assert resp.status_code == 400
    resp = await async_client.post(
        f"/api/v1/ai-assistance/messages/{a1.id}/edit",
        headers=headers,
        json={"content": "nope"},
    )
    assert resp.status_code == 400

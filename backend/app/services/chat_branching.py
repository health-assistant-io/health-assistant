"""Branch-tree helpers for chat messages (family contract, plan 11 §6).

Pure functions over ``ChatMessage`` rows: the visible conversation is the
active-path walk (pointer walk from the session's ``active_root_id``, per-node
``active_child_id`` pointers with newest-sibling fallback, cycle-safe), and
variant decoration (``variant_index`` / ``variant_count`` / ``sibling_ids``)
is attached in memory for the list endpoints.

Also derives health's LangGraph ``thread_id`` per branch (the plan 11 §6
health ruling): a NEW branch gets a NEW thread ``{session_id}:{tip_id}``
while the linear/legacy lineage keeps the bare session id — history
reconstruction replays rows (never checkpoints), so a paused ``ask_user``
interrupt on one branch survives turns streamed on any other branch.

The divergence rule, evaluated identically at turn time and at resume time:

- **thread of a turn** anchored on message ``m``: walk ``m``'s ancestor
  chain to the root; the root-most message that is NOT its parent's
  first-born marks where this branch left the original spine. No such
  message ⇒ the anchor sits on the original spine ⇒ legacy thread
  ``str(session_id)`` (pre-branching pauses stay resumable). Otherwise the
  thread is ``f"{session_id}:{divergence.id}"``.
- **regenerate turns** re-roll under an existing user message ``U``: the
  reply becomes a NEW assistant sibling of ``U``'s previous replies, so the
  turn — and any card it pauses on — keys the thread on
  ``f"{session_id}:{U.id}"`` (the branch tip the re-roll hangs from).
- **thread of a HITL card** on assistant message ``M`` (resume side): if
  ``M`` is a non-first-born assistant sibling (a regenerated variant) the
  pause belongs to ``M.parent_id``'s re-roll thread; otherwise the chain
  divergence rule above, applied to ``M``.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from typing import Any
from uuid import UUID

from app.models.chat_model import ChatMessage, ChatSession

EXCERPT_LEN = 100


def sort_key(message: ChatMessage):
    return (message.created_at, message.id)


def children_index(
    all_messages: list[ChatMessage],
) -> dict[UUID | None, list[ChatMessage]]:
    """Sibling lists per parent, oldest first."""
    children: dict[UUID | None, list[ChatMessage]] = defaultdict(list)
    for message in sorted(all_messages, key=sort_key):
        children[message.parent_id].append(message)
    return children


def walk_active_path(
    all_messages: list[ChatMessage], active_root_id: UUID | None
) -> list[ChatMessage]:
    """The visible conversation: pointer walk from the active root,
    active-child pointers with newest-sibling fallback, cycle-safe."""
    by_id = {message.id: message for message in all_messages}
    children = children_index(all_messages)

    start = active_root_id if active_root_id in by_id else None
    if start is None:
        roots = children.get(None, [])
        start = roots[-1].id if roots else None

    path: list[ChatMessage] = []
    visited: set[UUID] = set()
    current = start
    while current is not None and current not in visited:
        node = by_id.get(current)
        if node is None:
            break
        visited.add(current)
        path.append(node)
        kids = children.get(current, [])
        nxt = node.active_child_id if node.active_child_id in by_id else None
        if nxt is None and kids:
            nxt = kids[-1].id
        current = nxt
    return path


def decorate(visible: list[ChatMessage], all_messages: list[ChatMessage]) -> None:
    """Attach sibling/variant info in-memory for the list schemas."""
    children = children_index(all_messages)
    for message in visible:
        siblings = children.get(message.parent_id, [message])
        ids = [sibling.id for sibling in siblings]
        object.__setattr__(message, "sibling_ids", ids)
        object.__setattr__(message, "variant_count", len(ids))
        try:
            object.__setattr__(message, "variant_index", ids.index(message.id) + 1)
        except ValueError:  # pragma: no cover - defensive
            object.__setattr__(message, "variant_index", 1)


def activate_path_to(
    by_id: dict[UUID, ChatMessage], session: ChatSession, target: ChatMessage
) -> None:
    """Flip pointers so the active path passes through ``target`` (used by
    off-path regeneration). Root stays untouched unless the chain's first
    node IS a root-level variant."""
    chain: list[ChatMessage] = []
    current: ChatMessage | None = target
    while current is not None:
        chain.append(current)
        current = by_id.get(current.parent_id) if current.parent_id else None
    chain.reverse()
    if chain and chain[0].parent_id is None:
        session.active_root_id = chain[0].id
    for parent, child in itertools.pairwise(chain):
        parent.active_child_id = child.id


def ancestor_chain(by_id: dict[UUID, ChatMessage], message: ChatMessage) -> list[ChatMessage]:
    """``message``'s ancestor chain, root first (inclusive of ``message``)."""
    chain: list[ChatMessage] = []
    current: ChatMessage | None = message
    while current is not None:
        chain.append(current)
        current = by_id.get(current.parent_id) if current.parent_id else None
    chain.reverse()
    return chain


def is_first_born(message: ChatMessage, children: dict[UUID | None, list[ChatMessage]]) -> bool:
    siblings = children.get(message.parent_id, [])
    return not siblings or siblings[0].id == message.id


def first_divergence(
    chain: list[ChatMessage], children: dict[UUID | None, list[ChatMessage]]
) -> ChatMessage | None:
    """The root-most chain member that is not its parent's first-born —
    where this branch left the original spine."""
    for message in chain:  # root-first order
        if not is_first_born(message, children):
            return message
    return None


def branch_thread_key(
    session_id: UUID, all_messages: list[ChatMessage], anchor: ChatMessage
) -> str:
    """Thread id for anything anchored on ``anchor`` (a turn's trigger or a
    HITL card) — the plan 11 §6 health ruling, role-aware so every
    composition of edits and re-rolls derives ONE stable key per lineage:

    * the DEEPEST user-message variant on the chain (an edit sibling) is
      the branch's root divergence ⇒ ``{session_id}:{edited.id}`` — an
      edit is a new branch by construction and gets a fresh thread;
    * otherwise the ROOT-MOST assistant variant (a regenerated sibling)
      marks a re-roll lineage ⇒ ``{session_id}:{reroll_anchor.id}`` (the
      variant's parent — determinable before the new sibling exists);
    * no variant on the chain ⇒ the anchor sits on the original spine ⇒
      the legacy bare ``str(session_id)`` (pre-branching pauses stay
      resumable).
    """
    by_id = _by_id(all_messages)
    children = children_index(all_messages)
    chain = ancestor_chain(by_id, anchor)

    user_markers = [m for m in chain if m.role == "user" and not is_first_born(m, children)]
    if user_markers:
        return f"{session_id}:{user_markers[-1].id}"
    for message in chain:  # root-first
        if message.role == "assistant" and not is_first_born(message, children):
            return f"{session_id}:{message.parent_id}"
    return str(session_id)


def regenerate_thread_key(
    session_id: UUID, all_messages: list[ChatMessage], target_user_message: ChatMessage
) -> str:
    """Thread id a RE-ROLL under ``target_user_message`` runs on: the branch
    rule of the target's chain, except that a re-roll WITH an existing reply
    creates a fresh assistant variant at the anchor — keying the anchor's
    own thread (``{session_id}:{target.id}``) on an otherwise clean spine.
    A first reply under a childless target is just a send and keeps the
    chain's key."""
    key = branch_thread_key(session_id, all_messages, target_user_message)
    if key != str(session_id):
        return key
    children = children_index(all_messages)
    if children.get(target_user_message.id):
        return f"{session_id}:{target_user_message.id}"
    return str(session_id)


def card_thread_key(
    session_id: UUID, all_messages: list[ChatMessage], card_message: ChatMessage
) -> str:
    """Thread a paused turn keyed on, derived from the card-bearing message
    (resume side — cards attach to messages, not thread state). Identical
    rule to the turn side, so the two always agree."""
    return branch_thread_key(session_id, all_messages, card_message)


def _by_id(all_messages: list[ChatMessage]) -> dict[UUID, ChatMessage]:
    return {message.id: message for message in all_messages}


def tree_projection(all_messages: list[ChatMessage], active_root_id: UUID | None) -> dict[str, Any]:
    """Read-only ``{active_root_id, nodes[]}`` projection for the tree rail."""
    children: dict[UUID | None, list[UUID]] = defaultdict(list)
    for message in sorted(all_messages, key=sort_key):
        children[message.parent_id].append(message.id)
    nodes = [
        {
            "id": message.id,
            "role": message.role,
            "excerpt": _excerpt(message),
            "parent_id": message.parent_id,
            "children": children.get(message.id, []),
            "active_child_id": message.active_child_id,
        }
        for message in sorted(all_messages, key=sort_key)
    ]
    return {"active_root_id": active_root_id, "nodes": nodes}


def _excerpt(message: ChatMessage) -> str:
    content = message.content if isinstance(message.content, dict) else {}
    text = content.get("text", "")
    return (text or "")[:EXCERPT_LEN]

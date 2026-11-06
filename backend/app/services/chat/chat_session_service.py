from uuid import UUID

from sqlalchemy import and_, delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.chat_model import ChatMessage, ChatSession
from app.models.enums import ProposalSource
from app.models.proposal import Proposal
from app.services.chat.chat_branching import (
    activate_path_to,
    decorate,
    tree_projection,
    walk_active_path,
)
from app.services.proposals import proposal_service


def is_internal_message(content) -> bool:
    """True for messages that are LLM-internal artifacts and must never render
    as a chat bubble in the UI.

    The HITL resume continuation persists a structured ``[HITL RESOLUTION
    FEEDBACK]`` user message so the agent receives the user's task-card
    outcomes on its next turn. It MUST reconstruct into LLM history (so
    ``reconstruct_history`` still needs it from ``get_session_messages``), but
    it must NOT surface to the user — the GET /messages endpoint filters it
    out via this helper.

    Recognizes both the explicit ``kind="hitl_feedback"`` tag (new rows) and
    the ``[HITL RESOLUTION FEEDBACK]`` text prefix (rows persisted before the
    tag was introduced).
    """
    if not isinstance(content, dict):
        return False
    if content.get("kind") == "hitl_feedback":
        return True
    text = content.get("text")
    return bool(isinstance(text, str) and text.startswith("[HITL RESOLUTION FEEDBACK]"))


class ChatSessionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_sessions(
        self, user_id: UUID, tenant_id: UUID, patient_id: UUID | None = None
    ) -> list[ChatSession]:
        query = select(ChatSession).where(
            and_(ChatSession.user_id == user_id, ChatSession.tenant_id == tenant_id)
        )
        if patient_id:
            query = query.where(ChatSession.patient_id == patient_id)

        query = query.order_by(desc(ChatSession.updated_at))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_owned_session(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID
    ) -> ChatSession | None:
        result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        return result.scalars().first()

    async def get_session_messages(
        self,
        session_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        *,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[ChatMessage]:
        """Load a session's messages after verifying ownership.

        Args:
            session_id: The session to read.
            user_id: Owning user (isolation check).
            tenant_id: Owning tenant (isolation check).
            limit: Cap on the rows returned (``None`` = unbounded, the
                engine/history-reconstruction default).
            newest_first: Flip the ordering to ``created_at`` desc (the
                bridge's paged messages read); default is conversation
                order (oldest first).

        Returns:
            The matched messages; empty when the session is not the
            caller's.
        """
        # Verify ownership
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        session = session_result.scalars().first()
        if not session:
            return []

        query = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc() if newest_first else ChatMessage.created_at)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await self.db.execute(query)
        messages = list(result.scalars().all())
        await proposal_service.attach_cards(self.db, messages)
        return messages

    async def get_active_path_messages(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID
    ) -> list[ChatMessage]:
        """The visible (active-path) messages of an owned session, in
        conversation order — the family branching contract's walk. Empty
        list when the session does not exist or is not the caller's."""
        session = await self.get_owned_session(session_id, user_id, tenant_id)
        if session is None:
            return []
        all_messages = await self._all_messages(session_id)
        visible = walk_active_path(all_messages, session.active_root_id)
        await proposal_service.attach_cards(self.db, visible)
        return visible

    async def get_active_path_decorated(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID
    ) -> list[ChatMessage]:
        """Active-path messages with in-memory variant decoration
        (``variant_index`` / ``variant_count`` / ``sibling_ids``)."""
        session = await self.get_owned_session(session_id, user_id, tenant_id)
        if session is None:
            return []
        all_messages = await self._all_messages(session_id)
        visible = walk_active_path(all_messages, session.active_root_id)
        decorate(visible, all_messages)
        await proposal_service.attach_cards(self.db, visible)
        return visible

    async def _all_messages(self, session_id: UUID) -> list[ChatMessage]:
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        return list(result.scalars().all())

    async def create_session(
        self,
        user_id: UUID,
        tenant_id: UUID,
        patient_id: UUID | None = None,
        title: str = "New Chat",
    ) -> ChatSession:
        session = ChatSession(
            user_id=user_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            title=title,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def save_message(
        self,
        session_id: UUID,
        role: str,
        content: dict,
        tool_calls: list | None = None,
        citations: list | None = None,
        tasks: list | None = None,
        owner_user_id: UUID | None = None,
        parent_id: UUID | None = None,
    ) -> ChatMessage:
        """Persist one message.

        ``owner_user_id`` (audit 2026-09-11 S-1): when given, the target
        session must belong to that user — a foreign ``session_id`` raises
        ``LookupError`` instead of writing into another member's session.
        Callers that already verified ownership (e.g. the graph loop saving
        assistant turns after the dispatch-level check) may omit it.

        Branch placement (plan 13 H2): the message joins the session's tree
        under ``parent_id`` when given (the parent's ``active_child_id``
        flips to the new row — branch-local), else under the active-path
        tip (the session's ``active_root_id`` when the tree is empty).
        """
        session_result = await self.db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = session_result.scalars().first()
        if session is None or (owner_user_id is not None and session.user_id != owner_user_id):
            raise LookupError("Chat session not found.")

        parent: ChatMessage | None = None
        if parent_id is not None:
            parent_result = await self.db.execute(
                select(ChatMessage).where(
                    and_(ChatMessage.id == parent_id, ChatMessage.session_id == session_id)
                )
            )
            parent = parent_result.scalars().first()
            if parent is None:
                raise LookupError("Parent message not found.")
        else:
            all_messages = await self._all_messages(session_id)
            path = walk_active_path(all_messages, session.active_root_id)
            parent = path[-1] if path else None

        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            citations=citations,
            parent_id=parent.id if parent is not None else None,
        )
        self.db.add(message)
        # Flush first: the pointer flip needs the new row's DB-generated id
        # (assigning pre-flush writes NULL — the career 0014 lesson).
        await self.db.flush()
        if tasks:
            await proposal_service.upsert_chat_proposals(
                self.db,
                message=message,
                tasks=tasks,
                tenant_id=session.tenant_id,
                user_id=session.user_id,
            )
        if parent is not None:
            parent.active_child_id = message.id
        else:
            session.active_root_id = message.id
        session.updated_at = func.now()
        await self.db.commit()
        await self.db.refresh(message)
        await proposal_service.attach_cards(self.db, [message])
        return message

    async def get_message(
        self,
        message_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> ChatMessage | None:
        """Load a single message, verifying ownership via its session."""
        result = await self.db.execute(
            select(ChatMessage)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(
                and_(
                    ChatMessage.id == message_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        return result.scalars().first()

    async def edit_message(
        self,
        message_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        content: dict,
    ) -> tuple[ChatSession, ChatMessage]:
        """Branch a user message (family contract): a new sibling under the
        same parent carrying the edited content, the parent's
        ``active_child_id`` flipped to it (or the session's
        ``active_root_id`` for root-level variants). The old subtree stays
        as a switchable variant. Returns (session, edited message)."""
        message = await self.get_message(message_id, user_id, tenant_id)
        if message is None:
            raise LookupError("Message not found or access denied.")
        if message.role != "user":
            raise ValueError("Only user messages can be edited.")

        session = await self.get_owned_session(message.session_id, user_id, tenant_id)
        assert session is not None

        edited = ChatMessage(
            session_id=session.id,
            role="user",
            content=content,
            parent_id=message.parent_id,
        )
        # Edited resend: images the caller re-sent replace; an absent image
        # key inherits the original message's attachments (the edit changed
        # the text — career's edit ruling).
        original_images = (
            message.content.get("images")
            if isinstance(message.content, dict) and message.content.get("images")
            else None
        )
        if original_images and "images" not in edited.content:
            edited.content = {**edited.content, "images": original_images}
        self.db.add(edited)
        await self.db.flush()

        if message.parent_id is not None:
            parent = await self.db.get(ChatMessage, message.parent_id)
            if parent is not None:
                parent.active_child_id = edited.id
        else:
            session.active_root_id = edited.id
        session.updated_at = func.now()
        await self.db.commit()
        await self.db.refresh(edited)
        return session, edited

    async def prepare_regenerate(
        self,
        message_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> tuple[ChatSession, ChatMessage]:
        """Prepare a re-roll under the target USER message: activate the
        path to it when it is off-path. The new assistant sibling is
        persisted by the turn itself (parented explicitly under the
        target). Returns (session, target user message)."""
        message = await self.get_message(message_id, user_id, tenant_id)
        if message is None:
            raise LookupError("Message not found or access denied.")
        if message.role != "user":
            raise ValueError("Regenerate targets a user message.")

        session = await self.get_owned_session(message.session_id, user_id, tenant_id)
        assert session is not None

        all_messages = await self._all_messages(session.id)
        by_id = {m.id: m for m in all_messages}
        path_ids = {m.id for m in walk_active_path(all_messages, session.active_root_id)}
        if message.id not in path_ids:
            activate_path_to(by_id, session, message)
            await self.db.commit()
        return session, message

    async def select_message(
        self,
        message_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> list[ChatMessage]:
        """Flip exactly ONE pointer — the parent's active child (or the
        session's active root for root-level variants) — level-flip
        semantics, no ancestor-chain activation. Returns the re-walked,
        decorated visible path."""
        message = await self.get_message(message_id, user_id, tenant_id)
        if message is None:
            raise LookupError("Message not found or access denied.")

        session = await self.get_owned_session(message.session_id, user_id, tenant_id)
        assert session is not None

        all_messages = await self._all_messages(session.id)
        by_id = {m.id: m for m in all_messages}
        if message.parent_id is not None and message.parent_id in by_id:
            by_id[message.parent_id].active_child_id = message.id
        else:
            session.active_root_id = message.id
        session.updated_at = func.now()
        await self.db.commit()

        refreshed = await self._all_messages(session.id)
        visible = walk_active_path(refreshed, session.active_root_id)
        decorate(visible, refreshed)
        return visible

    async def tree(
        self,
        session_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> dict | None:
        """Read-only branch-tree projection for the graph rail. ``None``
        when the session is not the caller's."""
        session = await self.get_owned_session(session_id, user_id, tenant_id)
        if session is None:
            return None
        all_messages = await self._all_messages(session_id)
        return tree_projection(all_messages, session.active_root_id)

    async def find_message_by_proposal(
        self,
        session_id: UUID,
        proposal_id: str,
        user_id: UUID,
        tenant_id: UUID,
    ) -> ChatMessage | None:
        """The message that emitted a given proposal id (session-scoped).

        Proposal ids are globally unique, so this is a point lookup into the one
        proposals table (``source='chat'``, ``source_ref`` = message id) after
        the session ownership check.
        """
        try:
            proposal_uuid = UUID(str(proposal_id))
        except (TypeError, ValueError):
            return None
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalars().first():
            return None

        result = await self.db.execute(
            select(Proposal.id, Proposal.source_ref)
            .join(ChatMessage, Proposal.source_ref == ChatMessage.id)
            .where(
                Proposal.id == proposal_uuid,
                Proposal.source == ProposalSource.CHAT,
                ChatMessage.session_id == session_id,
            )
        )
        hit = result.first()
        if hit is None:
            return None
        message = await self.db.get(ChatMessage, hit.source_ref)
        if message is not None:
            await proposal_service.attach_cards(self.db, [message])
        return message

    async def find_resumable_message(
        self,
        session_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        message_id: UUID | None = None,
    ) -> ChatMessage | None:
        """Locate the message whose HITL cards should drive a resume turn.

        If ``message_id`` is provided, load it directly (when it carries
        proposals); otherwise pick the most recent message in the session that
        has proposals. Always verifies session ownership.
        """
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalars().first():
            return None

        card_messages = select(Proposal.source_ref).where(
            Proposal.source == ProposalSource.CHAT,
            Proposal.tenant_id == tenant_id,
        )

        filters = [
            ChatMessage.session_id == session_id,
            ChatMessage.id.in_(card_messages),
        ]
        if message_id:
            filters.append(ChatMessage.id == message_id)
        result = await self.db.execute(
            select(ChatMessage)
            .where(and_(*filters))
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        message = result.scalars().first()
        if message is not None:
            await proposal_service.attach_cards(self.db, [message])
        return message

    async def update_message_fields(
        self,
        message: ChatMessage,
        content: dict | None = None,
        tool_calls: list | None = None,
        citations: list | None = None,
        tasks: list | None = None,
    ) -> ChatMessage:
        """Patch one or more message fields in place.

        Used by the chat stream to proactively persist a HITL card as soon as
        it is emitted, then patch the message with the final content once the
        reasoning loop completes. ``tasks`` lands in the proposals table
        (idempotent by proposal id), never on the message row.
        """
        changed = False
        if content is not None:
            message.content = content
            flag_modified(message, "content")
            changed = True
        if tool_calls is not None:
            message.tool_calls = tool_calls
            flag_modified(message, "tool_calls")
            changed = True
        if citations is not None:
            message.citations = citations
            flag_modified(message, "citations")
            changed = True
        if tasks is not None:
            session = (
                (
                    await self.db.execute(
                        select(ChatSession).where(ChatSession.id == message.session_id)
                    )
                )
                .scalars()
                .first()
            )
            if session is None:
                raise LookupError("Chat session not found.")
            await proposal_service.upsert_chat_proposals(
                self.db,
                message=message,
                tasks=tasks,
                tenant_id=session.tenant_id,
                user_id=session.user_id,
            )
            changed = True
        if changed:
            await self.db.commit()
            await self.db.refresh(message)
            await proposal_service.attach_cards(self.db, [message])
        return message

    async def update_session_title(self, session_id: UUID, user_id: UUID, title: str):
        await self.db.execute(
            update(ChatSession)
            .where(and_(ChatSession.id == session_id, ChatSession.user_id == user_id))
            .values(title=title)
        )
        await self.db.commit()

    async def rename_session(
        self,
        session_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        title: str,
    ) -> ChatSession | None:
        """Rename a chat session owned by the caller (tenant-scoped).

        Returns ``None`` when the session does not exist or belongs to
        another user/tenant — the endpoint maps that to a 404.
        """
        session = await self.get_owned_session(session_id, user_id, tenant_id)
        if session is None:
            return None
        session.title = title
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def delete_session(self, session_id: UUID, user_id: UUID):
        # Chat proposals use a polymorphic ``source_ref`` (no FK), so the
        # session cascade does not reach them — delete them explicitly before
        # the message rows disappear (plan 5 data lifecycle).
        message_ids = select(ChatMessage.id).where(ChatMessage.session_id == session_id)
        await self.db.execute(
            delete(Proposal).where(
                Proposal.source == ProposalSource.CHAT,
                Proposal.source_ref.in_(message_ids),
            )
        )
        await self.db.execute(
            delete(ChatSession).where(
                and_(ChatSession.id == session_id, ChatSession.user_id == user_id)
            )
        )
        await self.db.commit()

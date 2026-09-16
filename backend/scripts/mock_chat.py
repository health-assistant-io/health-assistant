#!/usr/bin/env python3
"""Chat with the demo AI from the terminal — no browser, no API key needed.

Runs the same LangGraph chat engine (real tools, real DB) the app uses, with
the LLM resolved through the standard provider path. Against the seeded demo
DB this picks up the mock provider (``scripts/seed_demo.py`` → "Mock Medical
LLM (demo)"), so the scripted answer exercises the full tool pipeline
offline. Point it at a real provider via Administration → AI configuration.

Usage (from backend/, with the project venv):

    venv/bin/python scripts/mock_chat.py                     # default demo question
    venv/bin/python scripts/mock_chat.py --message "..."     # custom question
    venv/bin/python scripts/mock_chat.py --no-stream         # single-shot print
    venv/bin/python scripts/mock_chat.py --patient 33333333-3333-4333-8333-333333333301

Exit codes: 0 ok, 1 runtime failure, 2 usage/environment error.
"""

import argparse
import asyncio
import os
import sys
from uuid import UUID

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE  # noqa: E402
from app.models.fhir.patient import Patient  # noqa: E402
from app.models.tenant_model import TenantModel  # noqa: E402
from app.models.user_model import UserModel  # noqa: E402
from scripts.seed_demo import (  # noqa: E402
    DEMO_PATIENT_IDS,
    DEMO_TENANT_ID,
    DEMO_USER_ID,
)

DEFAULT_MESSAGE = "Provide me with the results of my latest examination"

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal chat against the demo AI provider path.")
    parser.add_argument("--message", "-m", default=DEFAULT_MESSAGE, help=f"question to ask (default: {DEFAULT_MESSAGE!r})")
    parser.add_argument("--tenant", default=str(DEMO_TENANT_ID), help="tenant UUID (default: demo tenant)")
    parser.add_argument("--user", default=str(DEMO_USER_ID), help="user UUID (default: demo admin)")
    parser.add_argument(
        "--patient",
        default=str(DEMO_PATIENT_IDS[0]),
        help="patient UUID (default: Maria Papadopoulou, the primary demo patient)",
    )
    parser.add_argument("--no-stream", action="store_true", help="print the whole answer at once")
    return parser.parse_args()


async def resolve_context(db, tenant_id: UUID, user_id: UUID, patient_id: UUID):
    tenant = (await db.execute(select(TenantModel).where(TenantModel.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        print(f"❌ Tenant {tenant_id} not found — run scripts/seed_demo.py first.")
        sys.exit(2)
    user = (await db.execute(select(UserModel).where(UserModel.id == user_id))).scalar_one_or_none()
    if not user:
        print(f"❌ User {user_id} not found — run scripts/seed_demo.py first.")
        sys.exit(2)
    patient = (await db.execute(select(Patient).where(Patient.id == patient_id))).scalar_one_or_none()
    if not patient:
        print(f"❌ Patient {patient_id} not found — run scripts/seed_demo.py first.")
        sys.exit(2)
    return tenant, user, patient


async def main() -> None:
    args = parse_args()
    if not DATABASE_AVAILABLE:
        print("❌ Database is not available. Check DATABASE_URL in backend/.env")
        sys.exit(1)

    tenant_id, user_id, patient_id = UUID(args.tenant), UUID(args.user), UUID(args.patient)

    from app.ai.agents.chat_agent import build_chat_tools  # noqa: E402
    from app.ai.graphs.chat_agent import chat_engine_iter  # noqa: E402
    from app.ai.providers.service import AIProviderService  # noqa: E402

    async with AsyncSessionLocal() as db:
        tenant, user, patient = await resolve_context(db, tenant_id, user_id, patient_id)
        print(f"{DIM}tenant={tenant.slug} user={user.email} patient MRN={patient.mrn}{RESET}")

        llm = await AIProviderService(db).get_llm("chat", tenant_id, user_id)
        print(f"{DIM}model: {getattr(llm, 'model_name', type(llm).__name__)}{RESET}\n")

        tools = await build_chat_tools(db, tenant_id, str(patient_id), user_id, label="mock_chat")
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        history = [{"role": "user", "content": args.message}]

        if args.no_stream:
            final = []
            async for kind, data in chat_engine_iter(
                llm_with_tools,
                tools,
                _plain_history(args.message),
                max_iterations=8,
                streaming=False,
                log_label="mock_chat",
                user_id=user_id,
                tenant_id=tenant_id,
            ):
                _handle_event(kind, data, final, quiet=True)
            print("".join(final))
            return

        printed: list = []
        async for kind, data in chat_engine_iter(
            llm_with_tools,
            tools,
            _plain_history(args.message),
            max_iterations=8,
            streaming=True,
            log_label="mock_chat",
            user_id=user_id,
            tenant_id=tenant_id,
        ):
            _handle_event(kind, data, printed)
        print(RESET)


def _plain_history(text: str) -> list:
    from langchain_core.messages import HumanMessage

    return [HumanMessage(content=text)]


def _handle_event(kind: str, data, printed: list, quiet: bool = False) -> None:
    if kind == "content":
        if quiet:
            printed.append(data)
        else:
            print(data, end="", flush=True)
    elif kind == "tool_call_start" and not quiet:
        print(f"\n{BOLD}→ {data}{RESET}", end="", flush=True)
    elif kind == "tool_call_exec" and not quiet:
        print(f"{DIM} (ran){RESET}", end="", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

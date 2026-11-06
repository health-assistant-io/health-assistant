"""Replay the erasure ledger against a restored database.

Run by ``scripts/restore.sh`` immediately after ``pg_restore`` (and available
manually): every ``erasure_requests`` row with ``status='done'`` is re-applied,
so a backup taken before an erasure cannot resurrect the subject. Idempotent —
a subject that is already absent is a no-op.

    docker compose run --rm --no-deps backend \
        python scripts/replay_erasure_ledger.py
"""

import asyncio
import json

from sqlalchemy import text

from app.core.database import AsyncSessionLocal, engine


async def main() -> None:
    async with AsyncSessionLocal() as db:
        present = (await db.execute(text("SELECT to_regclass('public.erasure_requests')"))).scalar()
        if present is None:
            print("erasure ledger: table not present (pre-plan-6 backup) — skipping")
            return
        from app.services.data_lifecycle.erasure import replay_erasure_ledger

        result = await replay_erasure_ledger(db)
        print("erasure ledger replay:", json.dumps(result, default=str))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

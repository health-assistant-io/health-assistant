"""One-shot backfill: encrypt existing plaintext ``AIProviderModel.api_key`` rows.

Converts rows that predate at-rest encryption into the encrypted form.
Safe to re-run — rows that are already encrypted (``enc::<token>``) are
skipped.

Usage:
    cd backend && source venv/bin/activate
    PYTHONPATH=. python scripts/encrypt_existing_api_keys.py [--dry-run]

Requires ``INTEGRATION_SECRET_KEY`` to be set in the environment. Generate
one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Allow execution as ``python scripts/encrypt_existing_api_keys.py`` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.encryption import (  # noqa: E402
    encrypt_secret,
    is_encrypted,
)
from app.models.ai_provider_model import AIProviderModel  # noqa: E402


async def run(dry_run: bool = False) -> int:
    encrypted_count = 0
    skipped = 0
    empty = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AIProviderModel))
        providers = result.scalars().all()
        print(f"Found {len(providers)} AI provider rows")

        for p in providers:
            if not p.api_key:
                empty += 1
                continue
            if is_encrypted(p.api_key):
                skipped += 1
                continue
            plaintext = p.api_key
            new_value = encrypt_secret(plaintext)
            if new_value == plaintext:
                # Encryption helper fell back to plaintext (no key configured).
                print(
                    f"  [SKIP] provider {p.id} ({p.name}): no Fernet key — "
                    "would store plaintext. Set INTEGRATION_SECRET_KEY first."
                )
                continue
            print(f"  [ENC]  provider {p.id} ({p.name}): encrypting api_key")
            if not dry_run:
                p.api_key = new_value
                # Bypass any ORM deferral — set attribute directly.
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(p, "api_key")
            encrypted_count += 1

        if not dry_run and encrypted_count:
            await db.commit()

    print(
        f"\nDone. encrypted={encrypted_count} skipped(already encrypted)={skipped} "
        f"empty={empty} dry_run={dry_run}"
    )
    return 0 if dry_run or not encrypted_count else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without committing",
    )
    args = parser.parse_args()
    return asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())

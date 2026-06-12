#!/usr/bin/env python3
"""
Health Assistant - Document Upload and Processing Test Suite
Tests the complete document flow from upload to OCR extraction
"""

import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select, func
from app.models.document_model import DocumentModel
from app.models.user_model import UserModel
from app.models.tenant_model import TenantModel
from app.core.config import settings


async def test_database_connection():
    """Test 1: Verify database connection"""
    print("\n📊 TEST 1: Database Connection")
    print("-" * 50)
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.connect() as conn:
            result = await conn.execute(select(func.count(TenantModel.id)))
            tenant_count = result.scalar()
            print("✅ Connected to database")
            print(f"   Tenants: {tenant_count}")
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False


async def test_documents_exist():
    """Test 2: Check if documents exist in database"""
    print("\n📄 TEST 2: Documents in Database")
    print("-" * 50)
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with AsyncSession(engine) as session:
            result = await session.execute(select(DocumentModel))
            documents = result.scalars().all()

            print(f"✅ Found {len(documents)} documents")

            if documents:
                print("\n   Sample documents:")
                for doc in documents[:3]:
                    print(f"   - {doc.filename}")
                    print(f"     ID: {doc.id}")
                    print(f"     Status: {doc.status}")
                    print(f"     Progress: {doc.progress}%")
                    print(f"     Has text: {'✅' if doc.extracted_text else '❌'}")
                    print(f"     Updated: {doc.updated_at or 'NULL'}")

        await engine.dispose()
        return len(documents) > 0
    except Exception as e:
        print(f"❌ Error querying documents: {e}")
        return False


async def test_files_exist():
    """Test 3: Check if uploaded files exist on disk"""
    print("\n📁 TEST 3: Uploaded Files on Disk")
    print("-" * 50)
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with AsyncSession(engine) as session:
            result = await session.execute(select(DocumentModel))
            documents = result.scalars().all()

            existing_files = 0
            missing_files = 0

            for doc in documents:
                file_path = Path(str(doc.file_path))
                if file_path.exists():
                    existing_files += 1
                else:
                    missing_files += 1
                    print(f"   ❌ Missing: {doc.file_path}")

            print(f"✅ Existing files: {existing_files}")
            if missing_files > 0:
                print(f"⚠️  Missing files: {missing_files}")

            return existing_files == len(documents)
    except Exception as e:
        print(f"❌ Error checking files: {e}")
        return False


async def test_user_tenant_relationship():
    """Test 4: Verify user-tenant relationship"""
    print("\n👥 TEST 4: User-Tenant Relationship")
    print("-" * 50)
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with AsyncSession(engine) as session:
            # Get all users with their tenants
            result = await session.execute(
                select(UserModel, TenantModel).join(
                    TenantModel, UserModel.tenant_id == TenantModel.id
                )
            )
            users = result.all()

            print(f"✅ Found {len(users)} users with valid tenants")

            for user, tenant in users:
                print(f"   - User: {user.email} ({user.role})")
                print(f"     Tenant: {tenant.name}")

        await engine.dispose()
        return len(users) > 0
    except Exception as e:
        print(f"❌ Error checking relationships: {e}")
        return False


async def test_document_ownership():
    """Test 5: Verify document ownership"""
    print("\n🔐 TEST 5: Document Ownership")
    print("-" * 50)
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(DocumentModel, UserModel).join(
                    UserModel, DocumentModel.owner_id == UserModel.id
                )
            )
            docs_with_owners = result.all()

            # Get orphaned documents
            result = await session.execute(
                select(DocumentModel).where(
                    ~DocumentModel.owner_id.in_(select(UserModel.id))
                )
            )
            orphans = result.scalars().all()

            print(f"✅ Documents with owners: {len(docs_with_owners)}")
            if orphans:
                print(f"⚠️  Orphaned documents: {len(orphans)}")

            return len(orphans) == 0
    except Exception as e:
        print(f"❌ Error checking ownership: {e}")
        return False


async def test_extraction_status():
    """Test 6: Check extraction status"""
    print("\n🔍 TEST 6: OCR Extraction Status")
    print("-" * 50)
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(DocumentModel).where(DocumentModel.extracted_text is not None)
            )
            extracted = result.scalars().all()

            result = await session.execute(
                select(DocumentModel).where(DocumentModel.status == "processing")
            )
            processing = result.scalars().all()

            result = await session.execute(
                select(func.count(DocumentModel.id)).where(
                    DocumentModel.status == "uploaded"
                )
            )
            uploaded_count = result.scalar()

            print("📊 Status breakdown:")
            print(f"   - Uploaded (not processed): {uploaded_count}")
            print(f"   - Processing: {len(processing)}")
            print(f"   - Completed (has text): {len(extracted)}")

            if uploaded_count > 0:
                print(
                    f"\n⚠️  WARNING: {uploaded_count} documents have not been processed!"
                )
                print("   To process them, you need to:")
                print("   1. Start Redis: redis-server")
                print(
                    "   2. Start Celery worker: celery -A app.workers.celery_app worker --loglevel=info"
                )

            return len(extracted) > 0
    except Exception as e:
        print(f"❌ Error checking extraction: {e}")
        return False


async def main():
    """Run all tests"""
    print("=" * 60)
    print("Health Assistant - Document Processing Test Suite")
    print("=" * 60)

    results = {
        "Database Connection": await test_database_connection(),
        "Documents Exist": await test_documents_exist(),
        "Files Exist": await test_files_exist(),
        "User-Tenant Relationships": await test_user_tenant_relationship(),
        "Document Ownership": await test_document_ownership(),
        "OCR Extraction": await test_extraction_status(),
    }

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")

    passed_count = sum(results.values())
    total_count = len(results)

    print(f"\nTotal: {passed_count}/{total_count} tests passed")

    if passed_count == total_count:
        print("\n🎉 All tests passed! System is working correctly.")
        return 0
    else:
        print(
            f"\n⚠️  {total_count - passed_count} test(s) failed. Review the output above."
        )
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

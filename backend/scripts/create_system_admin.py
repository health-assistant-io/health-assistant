#!/usr/bin/env python3
"""
Create a System Administrator user for Health Assistant
"""

import os
import sys
import asyncio
import uuid
from typing import Optional

# Ensure the backend directory is in the path
# This script is located in backend/scripts/
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.security import get_password_hash
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.models.user_model import UserModel
from app.models.tenant_model import TenantModel
from app.models.enums import Role
from sqlalchemy import select

async def create_system_admin(email: str, password: str, tenant_name: str = "System Tenant"):
    """Create system admin user and associated tenant if needed"""

    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available")
        print("Please check your DATABASE_URL in backend/.env")
        return

    try:
        async with AsyncSessionLocal() as session:
            # 1. Check if user already exists
            result = await session.execute(
                select(UserModel).where(UserModel.email == email)
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                if existing_user.role == Role.SYSTEM_ADMIN:
                    print(f"⚠️  System Admin user already exists: {email}")
                    return
                else:
                    print(f"🔄 Updating existing user {email} to SYSTEM_ADMIN role...")
                    existing_user.role = Role.SYSTEM_ADMIN
                    await session.commit()
                    print(f"✅ User {email} promoted to SYSTEM_ADMIN.")
                    return

            # 2. Check if we have any tenant, otherwise create one
            # System admins usually belong to a top-level tenant
            result = await session.execute(select(TenantModel).limit(1))
            tenant = result.scalar_one_or_none()

            if not tenant:
                print(f"🏢 Creating {tenant_name}...")
                tenant = TenantModel(name=tenant_name, settings={"is_system": True})
                session.add(tenant)
                await session.flush()
            else:
                print(f"🏢 Using existing tenant: {tenant.name}")

            # 3. Create system admin user
            hashed_password = get_password_hash(password)

            admin_user = UserModel(
                email=email,
                hashed_password=hashed_password,
                role=Role.SYSTEM_ADMIN,
                tenant_id=tenant.id,
                settings={"is_initial_admin": True},
            )
            session.add(admin_user)
            await session.commit()

            print("✅ System Admin user created successfully!")
            print()
            print("=" * 60)
            print("System Admin Credentials:")
            print("=" * 60)
            print(f"Email:    {email}")
            print(f"Password: {password}")
            print(f"Role:     SYSTEM_ADMIN")
            print(f"Tenant:   {tenant.name}")
            print("=" * 60)

    except Exception as e:
        print(f"❌ Error creating system admin: {e}")
        import traceback
        traceback.print_exc()

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Create a Health Assistant System Administrator")
    parser.add_argument("--email", type=str, default="sysadmin@health-assistant.local", help="Admin email")
    parser.add_argument("--password", type=str, default="admin123", help="Admin password")
    parser.add_argument("--tenant", type=str, default="System Management", help="Tenant name if creation needed")
    
    args = parser.parse_args()
    
    print("Health Assistant - Creating System Admin")
    print("-" * 40)
    
    await create_system_admin(args.email, args.password, args.tenant)

if __name__ == "__main__":
    asyncio.run(main())

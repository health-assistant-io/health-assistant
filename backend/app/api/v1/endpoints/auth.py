from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from datetime import timedelta
from app.core.config import settings
from app.core.security import (
    create_access_token,
    verify_password,
    get_current_user_id,
    decode_access_token,
)
from app.services.user_service import (
    get_user_by_email,
    create_user as service_create_user,
)
from app.services.tenant_service import create_tenant
from app.schemas.auth import TokenResponse, TokenRefresh, UserRegister
from app.schemas.user import UserResponse

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Authenticate user and return tokens"""
    user = await get_user_by_email(form_data.username)

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    access_token = create_access_token(
        data={
            "sub": user.email,
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
        },
        expires_delta=access_token_expires,
    )

    refresh_token_expires = timedelta(days=7)
    refresh_token = create_access_token(
        data={
            "sub": user.email,
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
        },
        expires_delta=refresh_token_expires,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )


@router.post("/register", response_model=UserResponse)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user. Creates a new tenant if tenant_id is not provided."""
    from app.core.security import get_password_hash
    from sqlalchemy import func
    from app.models.user_model import UserModel

    user = await get_user_by_email(user_data.email)
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # Check if this is the first user in the system
    count_result = await db.execute(select(func.count()).select_from(UserModel))
    total_users = count_result.scalar() or 0
    is_first_user = total_users == 0

    tenant_id = user_data.tenant_id
    if not tenant_id:
        # Create a new tenant for the home user
        tenant_name = user_data.email.split("@")[0].capitalize()
        new_tenant = await create_tenant(name=f"{tenant_name} Household")
        if not new_tenant:
            raise HTTPException(
                status_code=500, detail="Could not create default tenant"
            )
        tenant_id = str(new_tenant.id)

    hashed_password = get_password_hash(user_data.password)
    
    # First user is SYSTEM_ADMIN + ADMIN (implicitly), others depend on context
    role = "SYSTEM_ADMIN" if is_first_user else "ADMIN" if not user_data.tenant_id else "USER"
    
    new_user = await service_create_user(
        email=user_data.email,
        hashed_password=hashed_password,
        tenant_id=tenant_id,
        role=role,
    )

    return new_user


@router.get("/validate")
async def validate_token(user_id: str = Depends(get_current_user_id)):
    """Validate current token"""
    return {"valid": True, "user_id": user_id}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(token_data: TokenRefresh):
    """Refresh access token"""
    payload = decode_access_token(token_data.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    access_token = create_access_token(
        data={
            "sub": payload.get("sub"),
            "user_id": payload.get("user_id"),
            "tenant_id": payload.get("tenant_id"),
            "role": payload.get("role"),
        },
        expires_delta=access_token_expires,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=token_data.refresh_token,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )

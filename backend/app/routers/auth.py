"""FastAPI router for user authentication (register, login, me)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

_limiter = Limiter(key_func=get_remote_address)
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..services.auth import create_access_token, hash_password, verify_password
from ..utils.auth_dependency import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    """Payload for creating a new user account."""

    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    """Payload for authenticating an existing user."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT access token envelope."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user profile."""

    id: int
    email: str
    is_active: bool


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@_limiter.limit("3/minute")
async def register(
    request: Request,
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Create a new user account and return a JWT."""
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
@_limiter.limit("5/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate a user and return a JWT."""
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Return the profile of the currently authenticated user."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
    )

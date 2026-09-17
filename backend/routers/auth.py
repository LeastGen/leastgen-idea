"""IdeaFlow — auth endpoints.

JWT-based auth with email/password login and signup.
Tokens stored in HTTP-only cookies for the web UI.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from backend.database import (
    authenticate_user,
    check_run_limit,
    create_user,
    get_user_by_id,
    get_user_runs,
    increment_run_count,
)

router = APIRouter()

# ── JWT Config ──────────────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("OPENRESEARCH_JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72

# ── Models ──


class SignupRequest(BaseModel):
    email: str = Field(..., description="User email")
    password: str = Field(..., min_length=6, description="Password (min 6 chars)")
    name: str = Field(default="", description="Display name")


class LoginRequest(BaseModel):
    email: str = Field(..., description="User email")
    password: str = Field(..., description="User password")


class AuthResponse(BaseModel):
    token: str
    user: dict[str, Any]


class UserProfile(BaseModel):
    id: str
    email: str
    name: str
    subscription_tier: str
    subscription_status: str
    runs_used: int
    runs_limit: int
    created_at: str


class RunRecord(BaseModel):
    id: str
    run_type: str
    query: str
    status: str
    tokens_used: int
    cost: float
    created_at: str
    completed_at: str | None = None


# ── Token helpers ──


def create_jwt(user_id: str) -> str:
    """Create a JWT token for a user."""
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict[str, Any] | None:
    """Decode a JWT token. Returns payload or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


# ── Auth dependency ──

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """Get the current user from JWT in cookie or Authorization header."""
    token = None

    # Try cookie first
    token = request.cookies.get("session")

    # Fall back to Authorization header
    if not token and credentials:
        token = credentials.credentials

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


# ── Routes ──


@router.post("/auth/signup", response_model=AuthResponse)
async def signup(req: SignupRequest, response: Response):
    """Create a new account."""
    existing = authenticate_user(req.email, req.password)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = create_user(req.email, req.password, req.name)
    if not user:
        raise HTTPException(status_code=409, detail="Email already registered")

    token = create_jwt(user["id"])
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRY_HOURS * 3600,
        secure=False,  # Set True in production with HTTPS
    )
    return AuthResponse(token=token, user=user)


@router.post("/auth/login", response_model=AuthResponse)
async def login(req: LoginRequest, response: Response):
    """Authenticate and get a session token."""
    user = authenticate_user(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_jwt(user["id"])
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRY_HOURS * 3600,
        secure=False,
    )
    return AuthResponse(token=token, user=user)


@router.post("/auth/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie("session")
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserProfile)
async def get_profile(user: dict[str, Any] = Depends(get_current_user)):
    """Get the current user's profile."""
    return UserProfile(
        id=user["id"],
        email=user["email"],
        name=user["name"],
        subscription_tier=user["subscription_tier"],
        subscription_status=user["subscription_status"],
        runs_used=user["runs_used"],
        runs_limit=user["runs_limit"],
        created_at=user["created_at"],
    )


@router.get("/auth/runs", response_model=list[RunRecord])
async def list_my_runs(user: dict[str, Any] = Depends(get_current_user)):
    """List runs for the current user."""
    runs = get_user_runs(user["id"])
    return [
        RunRecord(
            id=r["id"],
            run_type=r["run_type"],
            query=r["query"],
            status=r["status"],
            tokens_used=r["tokens_used"],
            cost=r["cost"],
            created_at=r["created_at"],
            completed_at=r.get("completed_at"),
        )
        for r in runs
    ]


@router.get("/auth/limits")
async def check_limits(user: dict[str, Any] = Depends(get_current_user)):
    """Check run limits for the current user."""
    can_run, used, limit = check_run_limit(user["id"])
    return {
        "can_run": can_run,
        "runs_used": used,
        "runs_limit": limit,
        "remaining": limit - used,
    }
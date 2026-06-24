"""Auth API: register, login, me."""
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.exc import IntegrityError
from app.store.auth_store import (
    create_user, get_user_by_username, get_user_by_id,
    get_user_role_ids, get_user_permissions, list_roles, verify_password, seed_defaults,
)
from app.middleware.auth import create_access_token, get_current_user
from app.models.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from app.store.db import get_db_ctx, KnowledgeBase

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW = 300  # seconds
_LOGIN_MAX_ATTEMPTS = 10


def _get_workspace_kb_id(user_id: str) -> str:
    with get_db_ctx() as session:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.owner_id == user_id).order_by(KnowledgeBase.created_at).first()
        return kb.id if kb else ""


def _check_rate_limit(key: str):
    now = time.time()
    window = _LOGIN_ATTEMPTS[key]
    window[:] = [t for t in window if now - t < _LOGIN_WINDOW]
    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    window.append(now)


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest):
    if get_user_by_username(body.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    user_role = next((r for r in list_roles() if r.name == "user"), None)
    role_ids = [user_role.id] if user_role else []
    try:
        user = create_user(
            username=body.username,
            password=body.password,
            display_name=body.display_name or body.username,
            email=body.email,
            role_ids=role_ids,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Username already exists")

    with get_db_ctx() as session:
        kb = KnowledgeBase(
            name=f"{user.display_name or user.username}的工作空间",
            visibility="public",
            owner_id=user.id,
        )
        session.add(kb)
        session.commit()
        workspace_kb_id = kb.id

    token = create_access_token({"sub": user.id, "username": user.username})
    permissions = get_user_permissions(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id, username=user.username,
            display_name=user.display_name, email=user.email,
            is_active=user.is_active, role_ids=role_ids,
            roles=[r.name for r in list_roles() if r.id in role_ids],
            permissions=permissions,
            workspace_kb_id=workspace_kb_id,
        ),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request):
    _check_rate_limit(request.client.host)
    user = get_user_by_username(body.username)
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is disabled")
    role_ids = get_user_role_ids(user.id)
    token = create_access_token({"sub": user.id, "username": user.username})
    roles = [r for r in list_roles() if r.id in role_ids]
    permissions = get_user_permissions(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id, username=user.username,
            display_name=user.display_name, email=user.email,
            is_active=user.is_active, role_ids=role_ids,
            roles=[r.name for r in roles],
            permissions=permissions,
            workspace_kb_id=_get_workspace_kb_id(user.id),
        ),
    )


@router.get("/me", response_model=UserResponse)
def me(current_user: dict = Depends(get_current_user)):
    user = get_user_by_id(current_user["id"])
    role_ids = get_user_role_ids(user.id)
    roles = [r for r in list_roles() if r.id in role_ids]
    permissions = get_user_permissions(user.id)
    return UserResponse(
        id=user.id, username=user.username,
        display_name=user.display_name, email=user.email,
        is_active=user.is_active, role_ids=role_ids,
        roles=[r.name for r in roles],
        permissions=permissions,
        workspace_kb_id=_get_workspace_kb_id(user.id),
    )

"""Auth API: register, login, me."""
import threading
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.exc import IntegrityError
from app.store.auth_store import (
    create_user, get_user_by_username, get_user_by_id,
    get_user_role_ids, get_user_permissions, list_roles, verify_password,
    hash_password, seed_defaults,
)
from app.middleware.auth import create_access_token, get_current_user
from app.models.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from app.store.db import get_db_ctx, KnowledgeBase

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_LOGIN_LOCK = threading.Lock()
_LOGIN_WINDOW = 300  # seconds
_LOGIN_MAX_ATTEMPTS = 10
_RATE_LIMIT_MAX_KEYS = 10000  # 桶数量上界：超出整体清空，防 key 只增不删的缓慢泄漏


def _client_ip(request) -> str:
    """限流桶的 key：反代部署时取 X-Forwarded-For 首段真实客户端 IP。

    旧实现直接用 request.client.host——反代后全体用户共享一个桶，
    要么互相锁死，要么限流形同虚设。
    """
    fwd = request.headers.get("x-forwarded-for", "")
    first = fwd.split(",")[0].strip() if fwd else ""
    return first or (request.client.host if request.client else "unknown")

# 假哈希：用户不存在时也跑一次 bcrypt，拉平两条路径的耗时，防用户名枚举
_DUMMY_HASH = hash_password("ragent-dummy-password-for-timing-equalization")


def _get_workspace_kb_id(user_id: str) -> str:
    with get_db_ctx() as session:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.owner_id == user_id).order_by(KnowledgeBase.created_at).first()
        return kb.id if kb else ""


def _check_rate_limit(key: str, message: str = "登录尝试过于频繁，请稍后再试"):
    # 限流桶是进程级 dict + 列表：多 worker 不可见（架构限制，按 CLAUDE.md
    # Next up 排队），但单 worker 内并发登录/注册会竞态（list[:]= 重建中间并发
    # append 可能丢计数导致限额失效）。整段锁内串行。
    with _LOGIN_LOCK:
        if len(_LOGIN_ATTEMPTS) > _RATE_LIMIT_MAX_KEYS:
            # key 只增不删会缓慢泄漏：超过上界整体清空重建（简单有界）
            _LOGIN_ATTEMPTS.clear()
        now = time.time()
        window = _LOGIN_ATTEMPTS[key]
        window[:] = [t for t in window if now - t < _LOGIN_WINDOW]
        if len(window) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail=message)
        window.append(now)


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, request: Request):
    _check_rate_limit("register:" + _client_ip(request),
                      message="注册过于频繁，请稍后再试")
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
        # 个人工作空间默认 restricted：chunk 检索走属主旁路 ACL，
        # 本人可查、他人不可见（旧默认 public = 全员可检索个人文档）
        kb = KnowledgeBase(
            name=f"{user.display_name or user.username}的工作空间",
            visibility="restricted",
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
    _check_rate_limit(_client_ip(request))
    user = get_user_by_username(body.username)
    if user is None:
        verify_password(body.password, _DUMMY_HASH)   # 时序拉平：存在/不存在耗时一致
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not verify_password(body.password, user.hashed_password):
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

"""JWT authentication middleware."""
import asyncio
import time
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.store.auth_store import get_user_by_id, get_user_permissions, get_user_role_ids

bearer_required = HTTPBearer(auto_error=True)
bearer_optional = HTTPBearer(auto_error=False)

_admin_role_id: int | None = None
_admin_role_ts: float = 0.0
_ADMIN_ROLE_TTL = 300.0   # 设计审查 P3-18：admin 角色 id 缓存加 TTL，避免永不过期

# 设计审查 P1-10：每请求 3 次 DB 查询（user + roles + permissions），
# 按 user_id 做进程内 TTL 缓存（60s）。角色/权限变更走 invalidate_user_cache。
_user_cache: dict[str, tuple[float, dict]] = {}
_USER_CACHE_TTL = 60.0


def invalidate_user_cache(user_id: str) -> None:
    """角色/权限变更后调用，使该用户下次鉴权立即重查而非等 TTL。"""
    _user_cache.pop(user_id, None)


def invalidate_admin_role() -> None:
    """设计审查 P3-18：admin 角色 id 缓存显式失效（角色变更后调用）。"""
    global _admin_role_id, _admin_role_ts
    _admin_role_id = None
    _admin_role_ts = 0.0


def _get_admin_role_id() -> int:
    global _admin_role_id, _admin_role_ts
    now = time.monotonic()
    if _admin_role_id is None or (now - _admin_role_ts) >= _ADMIN_ROLE_TTL:
        from app.store.db import Role, get_session
        session = get_session()
        try:
            role = session.query(Role).filter(Role.name == "admin").first()
            _admin_role_id = role.id if role else 0
            _admin_role_ts = now
        finally:
            session.close()
    return _admin_role_id


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def _build_user_dict(user) -> dict:
    now = time.monotonic()
    cached = _user_cache.get(user.id)
    if cached and (now - cached[0]) < _USER_CACHE_TTL:
        return cached[1]
    role_ids = get_user_role_ids(user.id)
    permissions = get_user_permissions(user.id)
    d = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role_ids": role_ids,
        "permissions": permissions,
        "is_admin": _get_admin_role_id() in role_ids,
    }
    _user_cache[user.id] = (now, d)
    return d


def _resolve_token(credentials: HTTPAuthorizationCredentials | None) -> dict | None:
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not user.is_active:
        return None
    return _build_user_dict(user)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_required),
) -> dict:
    """FastAPI dependency: returns {id, username, display_name, role_ids, permissions, is_admin}.

    Raises 401 if token missing or invalid.

    _resolve_token 是同步 DB 查询（user + roles + permissions 三连）——
    每个鉴权请求都会发生，必须 to_thread，否则阻塞事件循环。
    """
    result = await asyncio.to_thread(_resolve_token, credentials)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return result


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
) -> dict | None:
    """Like get_current_user but returns None instead of 401."""
    return await asyncio.to_thread(_resolve_token, credentials)

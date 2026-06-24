"""JWT authentication middleware."""
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from app.config import settings
from app.store.auth_store import get_user_by_id, get_user_role_ids, get_user_permissions

bearer_required = HTTPBearer(auto_error=True)
bearer_optional = HTTPBearer(auto_error=False)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def _build_user_dict(user) -> dict:
    role_ids = get_user_role_ids(user.id)
    permissions = get_user_permissions(user.id)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role_ids": role_ids,
        "permissions": permissions,
        "is_admin": "admin" in permissions,
    }


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
    """
    result = _resolve_token(credentials)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return result


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
) -> dict | None:
    """Like get_current_user but returns None instead of 401."""
    return _resolve_token(credentials)

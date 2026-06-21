"""Auth API: register, login, me."""
from fastapi import APIRouter, Depends, HTTPException, status
from app.store.auth_store import (
    create_user, get_user_by_username, get_user_by_id,
    get_user_role_ids, get_user_permissions, list_roles, verify_password, seed_defaults,
)
from app.middleware.auth import create_access_token, get_current_user
from app.models.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest):
    if get_user_by_username(body.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    user_role = next((r for r in list_roles() if r.name == "user"), None)
    role_ids = [user_role.id] if user_role else []
    user = create_user(
        username=body.username,
        password=body.password,
        display_name=body.display_name or body.username,
        email=body.email,
        role_ids=role_ids,
    )
    token = create_access_token({"sub": user.id, "username": user.username})
    permissions = get_user_permissions(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id, username=user.username,
            display_name=user.display_name, email=user.email,
            is_active=user.is_active, role_ids=role_ids,
            roles=[r.name for r in (list_roles() if False else [])],
            permissions=permissions,
        ),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
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
    )

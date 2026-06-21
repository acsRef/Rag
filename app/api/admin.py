"""Admin API: user & role management."""
from fastapi import APIRouter, Depends, HTTPException
from app.store.auth_store import (
    list_users, get_user_by_id, get_user_role_ids, set_user_roles,
    list_roles, create_role, set_role_permissions, get_role_permissions,
    seed_defaults,
)
from app.store.db import RolePermission
from app.middleware.auth import get_current_user
from app.models.schemas import UserResponse, UserRoleUpdateRequest

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(user: dict):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/users", response_model=list[UserResponse])
def list_all_users(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    users = list_users()
    result = []
    for u in users:
        role_ids = get_user_role_ids(u.id)
        roles = [r for r in list_roles() if r.id in role_ids]
        result.append(UserResponse(
            id=u.id, username=u.username,
            display_name=u.display_name, email=u.email,
            is_active=u.is_active, role_ids=role_ids,
            roles=[r.name for r in roles],
        ))
    return result


@router.put("/users/{user_id}/roles")
def update_user_roles(user_id: str, body: UserRoleUpdateRequest, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    set_user_roles(user_id, body.role_ids)
    return {"ok": True}


@router.get("/roles")
def list_all_roles(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    roles = list_roles()
    result = []
    for r in roles:
        perms = get_role_permissions(r.id)
        result.append({"id": r.id, "name": r.name, "description": r.description, "permissions": perms})
    return result


@router.post("/roles")
def create_new_role(name: str, description: str = "", permissions: list[str] = [],
                    current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    role = create_role(name, description)
    if permissions:
        set_role_permissions(role.id, permissions)
    return {"id": role.id, "name": role.name}

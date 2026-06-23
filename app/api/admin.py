"""Admin API: user & role management + PII review."""
from fastapi import APIRouter, Depends, HTTPException
from app.store.auth_store import (
    list_users, get_user_by_id, get_user_role_ids, set_user_roles,
    list_roles, create_role, set_role_permissions, get_role_permissions,
    seed_defaults,
)
from app.store.db import RolePermission
from app.store.db import get_db_ctx, PiiAlert, PiiHold, utc_now
from app.middleware.auth import get_current_user
from app.models.schemas import UserResponse, UserRoleUpdateRequest
from app.core.pii_scanner import invalidate_cache
from datetime import datetime
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(user: dict):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/users", response_model=list[UserResponse])
def list_all_users(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    from app.store.db import User, UserRole
    with get_db_ctx() as session:
        users = (
            session.query(User)
            .outerjoin(UserRole)
            .order_by(User.created_at.desc())
            .all()
        )
    all_roles = {r.id: r for r in list_roles()}
    result = []
    seen = set()
    for u in users:
        if u.id in seen:
            continue
        seen.add(u.id)
        role_ids = [ur.role_id for ur in u.user_roles] if hasattr(u, 'user_roles') else get_user_role_ids(u.id)
        roles = [all_roles[rid].name for rid in role_ids if rid in all_roles]
        result.append(UserResponse(
            id=u.id, username=u.username,
            display_name=u.display_name, email=u.email,
            is_active=u.is_active, role_ids=role_ids,
            roles=roles,
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


# ── PII Audit ────────────────────────────────────────────

class PiiAlertItem(BaseModel):
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat() if v else ""})
    id: int
    source_type: str
    source_id: str
    rule_name: str
    context_snippet: str
    strategy: str
    status: str
    created_at: datetime | None = None


@router.get("/pii-alerts", response_model=list[PiiAlertItem])
def list_pii_alerts(status: str = "pending", current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db_ctx() as session:
        alerts = (
            session.query(PiiAlert)
            .filter(PiiAlert.status == status)
            .order_by(PiiAlert.created_at.desc())
            .limit(50)
            .all()
        )
        return [
            PiiAlertItem(
                id=a.id,
                source_type=a.source_type,
                source_id=a.source_id,
                rule_name=a.rule_name,
                context_snippet=a.context_snippet,
                strategy=a.strategy,
                status=a.status,
                created_at=a.created_at,
            )
            for a in alerts
        ]


@router.post("/pii-alerts/{alert_id}/confirm")
def confirm_pii_alert(alert_id: int, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db_ctx() as session:
        alert = session.query(PiiAlert).filter(PiiAlert.id == alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert.status = "confirmed"
        alert.resolved_at = utc_now()
        session.commit()
        return {"ok": True}


@router.post("/pii-alerts/{alert_id}/false-positive")
def false_positive_pii_alert(alert_id: int, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db_ctx() as session:
        alert = session.query(PiiAlert).filter(PiiAlert.id == alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert.status = "false_positive"
        alert.resolved_at = utc_now()
        session.commit()

        hold = (
            session.query(PiiHold)
            .filter(
                PiiHold.source_type == alert.source_type,
                PiiHold.source_id == alert.source_id,
                PiiHold.status == "pending",
            )
            .first()
        )
        if hold:
            hold.status = "released"
            session.commit()
        return {"ok": True}


@router.post("/pii-alerts/{alert_id}/whitelist")
def whitelist_pii_alert(alert_id: int, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db_ctx() as session:
        alert = session.query(PiiAlert).filter(PiiAlert.id == alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        from app.store.db import SensitiveRule
        rule = session.query(SensitiveRule).filter(
            SensitiveRule.rule_name == alert.rule_name
        ).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")

        existing = (rule.exclusion_words or "").split(";")
        word = alert.matched_text[:20]
        if word not in existing:
            existing.append(word)
            rule.exclusion_words = ";".join(w for w in existing if w.strip())
            session.commit()

        alert.status = "false_positive"
        alert.resolved_at = utc_now()
        session.commit()

        hold = (
            session.query(PiiHold)
            .filter(
                PiiHold.source_type == alert.source_type,
                PiiHold.source_id == alert.source_id,
                PiiHold.status == "pending",
            )
            .first()
        )
        if hold:
            hold.status = "released"
            session.commit()

        invalidate_cache()
        return {"ok": True, "whitelisted": word}

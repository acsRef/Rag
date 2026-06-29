"""Admin API: user & role management + PII review."""
from fastapi import APIRouter, Depends, HTTPException
from app.store.auth_store import (
    get_user_role_ids, set_user_roles,
    list_roles, create_role, set_role_permissions, get_role_permissions,
)
from app.store.db import get_db_ctx, get_session, PiiAlert, PiiHold, utc_now, User, Role, Chunk, Document
from app.middleware.auth import get_current_user
from app.models.schemas import UserResponse, UserRoleUpdateRequest
from app.core.pii_scanner import invalidate_cache
from datetime import datetime
from pydantic import BaseModel
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(user: dict):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")


class CreateRoleRequest(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = []


@router.get("/users", response_model=list[UserResponse])
def list_all_users(current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db_ctx() as session:
        users = session.query(User).order_by(User.created_at.desc()).all()
    all_roles = {r.id: r for r in list_roles()}
    result = []
    for u in users:
        role_ids = get_user_role_ids(u.id)
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
    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if body.role_ids:
            existing_role_ids = {r.id for r in session.query(Role).all()}
            for rid in body.role_ids:
                if rid not in existing_role_ids:
                    raise HTTPException(status_code=400, detail=f"角色 {rid} 不存在")
        set_user_roles(user_id, body.role_ids)
        return {"ok": True}
    finally:
        session.close()


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
def create_new_role(body: CreateRoleRequest, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    role = create_role(body.name, body.description)
    if body.permissions:
        set_role_permissions(role.id, body.permissions)
    return {"id": role.id, "name": role.name}

class PiiAlertItem(BaseModel):
    id: int
    source_type: str
    source_id: str
    rule_name: str
    context_snippet: str
    strategy: str
    status: str
    created_at: datetime | None = None

VALID_PII_STATUSES = {"pending", "confirmed", "false_positive"}

@router.get("/pii-alerts", response_model=list[PiiAlertItem])
def list_pii_alerts(status: str = "pending", current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    if status not in VALID_PII_STATUSES:
        raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
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
        if alert.status != "pending":
            raise HTTPException(status_code=400, detail=f"Alert is already {alert.status}")
        alert.status = "confirmed"
        alert.resolved_at = utc_now()
        session.commit()
        return {"ok": True, "alert_id": alert_id, "new_status": "confirmed"}


@router.post("/pii-alerts/{alert_id}/false-positive")
def false_positive_pii_alert(alert_id: int, current_user: dict = Depends(get_current_user)):
    require_admin(current_user)
    with get_db_ctx() as session:
        alert = session.query(PiiAlert).filter(PiiAlert.id == alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        if alert.status != "pending":
            raise HTTPException(status_code=400, detail=f"Alert is already {alert.status}")
        alert.status = "false_positive"
        alert.resolved_at = utc_now()

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
        return {"ok": True, "alert_id": alert_id, "new_status": "false_positive"}


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
        word = alert.matched_text[:20].split(";")[0]
        if word not in existing:
            existing.append(word)
            rule.exclusion_words = ";".join(w for w in existing if w.strip())

        alert.status = "false_positive"
        alert.resolved_at = utc_now()

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


class ChunkInfo(BaseModel):
    chunk_id: str
    document_id: str
    kb_id: str
    filename: str
    title: str
    section_path: str
    text: str
    content_hash: str
    visibility: str


@router.get("/chunks", response_model=list[ChunkInfo])
def lookup_chunks(
    ids: str,
    current_user: dict = Depends(get_current_user),
):
    """Look up chunks by comma-separated chunk_ids.

    Returns chunk details including source document filename.
    Used by the diagnostics HTML page to show chunk provenance.
    """
    require_admin(current_user)
    if not ids:
        return []
    chunk_ids = [c.strip() for c in ids.split(",") if c.strip()]
    with get_db_ctx() as session:
        rows = (
            session.query(
                Chunk.chunk_id, Chunk.document_id, Chunk.kb_id,
                Chunk.title, Chunk.section_path, Chunk.text,
                Chunk.content_hash, Chunk.visibility,
                Document.filename,
            )
            .outerjoin(Document, Chunk.document_id == Document.document_id)
            .filter(Chunk.chunk_id.in_(chunk_ids))
            .all()
        )
        return [
            ChunkInfo(
                chunk_id=r.chunk_id, document_id=r.document_id, kb_id=r.kb_id,
                filename=r.filename or "", title=r.title or "",
                section_path=r.section_path or "", text=r.text[:500],
                content_hash=r.content_hash or "", visibility=r.visibility,
            )
            for r in rows
        ]

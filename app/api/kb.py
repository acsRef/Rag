"""Knowledge Base CRUD API."""
from fastapi import APIRouter, Depends, HTTPException
from app.store.db import get_session, KnowledgeBase, KBRoleAccess
from app.middleware.auth import get_current_user
from app.models.schemas import KBCreateRequest, KBResponse, KBRoleAccessRequest

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])


@router.post("", response_model=KBResponse)
def create_kb(body: KBCreateRequest, current_user: dict = Depends(get_current_user)):
    if current_user["is_admin"] is False and "kb.create" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    session = get_session()
    try:
        kb = KnowledgeBase(name=body.name, visibility=body.visibility, owner_id=current_user["id"])
        session.add(kb)
        session.commit()
        return KBResponse(id=kb.id, name=kb.name, visibility=kb.visibility, owner_id=kb.owner_id)
    finally:
        session.close()


@router.get("", response_model=list[KBResponse])
def list_kb(current_user: dict = Depends(get_current_user)):
    session = get_session()
    try:
        role_ids = current_user["role_ids"]
        can_read_all = current_user["is_admin"] or "doc.read_all" in current_user["permissions"]
        if can_read_all:
            kbs = session.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).all()
        else:
            kbs = session.query(KnowledgeBase).filter(
                (KnowledgeBase.visibility == "public") |
                (KnowledgeBase.visibility.in_(["internal", "restricted"]) &
                 KnowledgeBase.id.in_(
                     session.query(KBRoleAccess.kb_id).filter(KBRoleAccess.role_id.in_(role_ids)).subquery()
                 ))
            ).order_by(KnowledgeBase.created_at.desc()).all()
        return [KBResponse(id=k.id, name=k.name, visibility=k.visibility, owner_id=k.owner_id) for k in kbs]
    finally:
        session.close()


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, current_user: dict = Depends(get_current_user)):
    if not current_user["is_admin"] and "kb.delete" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    session = get_session()
    try:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise HTTPException(status_code=404, detail="KB not found")
        if not current_user["is_admin"] and kb.owner_id != current_user["id"]:
            raise HTTPException(status_code=403, detail="无权删除不属于自己的知识库")
        session.delete(kb)
        session.commit()
        return {"ok": True}
    finally:
        session.close()


@router.put("/{kb_id}/roles")
def set_kb_role_access(kb_id: str, body: KBRoleAccessRequest, current_user: dict = Depends(get_current_user)):
    if not current_user["is_admin"] and "kb.manage_visibility" not in current_user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    session = get_session()
    try:
        kb = session.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
        session.query(KBRoleAccess).filter(KBRoleAccess.kb_id == kb_id).delete()
        for rid in body.role_ids:
            session.add(KBRoleAccess(kb_id=kb_id, role_id=rid))
        session.commit()
        return {"ok": True}
    finally:
        session.close()

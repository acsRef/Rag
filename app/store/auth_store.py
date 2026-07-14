"""User & role CRUD operations."""
from app.store.db import get_session, User, Role, UserRole, RolePermission, new_id
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── User ────────────────────────────────────────────────

def create_user(username: str, password: str, display_name: str = "", email: str = "", role_ids: list[int] | None = None) -> User:
    session = get_session()
    try:
        user = User(
            id=new_id(),
            username=username,
            hashed_password=hash_password(password),
            display_name=display_name,
            email=email,
        )
        session.add(user)
        session.flush()
        for rid in (role_ids or []):
            session.add(UserRole(user_id=user.id, role_id=rid))
        session.commit()
        session.expunge(user)
        return user
    finally:
        session.close()


def get_user_by_username(username: str) -> User | None:
    session = get_session()
    try:
        return session.query(User).filter(User.username == username).first()
    finally:
        session.close()


def get_user_by_id(user_id: str) -> User | None:
    session = get_session()
    try:
        return session.query(User).filter(User.id == user_id).first()
    finally:
        session.close()


def list_users() -> list[User]:
    session = get_session()
    try:
        return session.query(User).order_by(User.created_at.desc()).all()
    finally:
        session.close()


def get_user_role_ids(user_id: str) -> list[int]:
    session = get_session()
    try:
        rows = session.query(UserRole.role_id).filter(UserRole.user_id == user_id).all()
        return [r[0] for r in rows]
    finally:
        session.close()


def get_user_roles(user_id: str) -> list[Role]:
    session = get_session()
    try:
        return session.query(Role).join(UserRole).filter(UserRole.user_id == user_id).all()
    finally:
        session.close()


def set_user_roles(user_id: str, role_ids: list[int]):
    session = get_session()
    try:
        session.query(UserRole).filter(UserRole.user_id == user_id).delete()
        for rid in role_ids:
            session.add(UserRole(user_id=user_id, role_id=rid))
        session.commit()
    finally:
        session.close()


# ── Role ────────────────────────────────────────────────

def create_role(name: str, description: str = "") -> Role:
    session = get_session()
    try:
        role = Role(name=name, description=description)
        session.add(role)
        session.commit()
        return role
    finally:
        session.close()


def get_role_by_name(name: str) -> Role | None:
    session = get_session()
    try:
        return session.query(Role).filter(Role.name == name).first()
    finally:
        session.close()


def list_roles() -> list[Role]:
    session = get_session()
    try:
        return session.query(Role).order_by(Role.id).all()
    finally:
        session.close()


# ── Permissions ─────────────────────────────────────────

def set_role_permissions(role_id: int, permissions: list[str]):
    session = get_session()
    try:
        session.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
        for p in permissions:
            session.add(RolePermission(role_id=role_id, permission=p))
        session.commit()
    finally:
        session.close()


def get_role_permissions(role_id: int) -> list[str]:
    session = get_session()
    try:
        rows = session.query(RolePermission.permission).filter(RolePermission.role_id == role_id).all()
        return [r[0] for r in rows]
    finally:
        session.close()


def get_user_permissions(user_id: str) -> list[str]:
    session = get_session()
    try:
        rows = session.query(RolePermission.permission)\
            .join(UserRole, UserRole.role_id == RolePermission.role_id)\
            .filter(UserRole.user_id == user_id).all()
        return list(set(r[0] for r in rows))
    finally:
        session.close()


# ── Seed ────────────────────────────────────────────────

def seed_defaults():
    session = get_session()
    try:
        if session.query(Role).count() == 0:
            admin_role = Role(name="admin", description="系统管理员")
            user_role = Role(name="user", description="普通用户")
            session.add_all([admin_role, user_role])
            session.flush()

            perms = {
                admin_role.id: ["chat", "doc.upload", "doc.delete", "doc.read_all", "kb.create",
                                "kb.delete", "kb.manage_visibility", "user.manage"],
                user_role.id: ["chat", "doc.upload", "doc.delete"],
            }
            for rid, plist in perms.items():
                for p in plist:
                    session.add(RolePermission(role_id=rid, permission=p))

            admin_pw = hash_password("admin123")
            admin = User(id=new_id(), username="admin", hashed_password=admin_pw,
                         display_name="Administrator", email="admin@ragent.local")
            session.add(admin)
            session.flush()
            session.add(UserRole(user_id=admin.id, role_id=admin_role.id))

        else:
            _ensure_permission(session, "admin", "doc.delete")
            _ensure_permission(session, "user", "doc.delete")

        # Seed anonymous user unconditionally (may be missing after re-seed)
        anon = session.query(User).filter(User.id == "anonymous").first()
        if not anon:
            anon_pw = hash_password(new_id())
            session.add(User(
                id="anonymous", username="anonymous", hashed_password=anon_pw,
                display_name="Anonymous User", email="", is_active=True,
            ))

        session.commit()
    finally:
        session.close()


def _ensure_permission(session, role_name: str, permission: str):
    from app.store.db import Role, RolePermission
    role = session.query(Role).filter(Role.name == role_name).first()
    if role:
        exists = session.query(RolePermission).filter(
            RolePermission.role_id == role.id,
            RolePermission.permission == permission,
        ).first()
        if not exists:
            session.add(RolePermission(role_id=role.id, permission=permission))

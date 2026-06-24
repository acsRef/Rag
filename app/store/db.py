"""All SQLAlchemy models + PG connection."""
from sqlalchemy import create_engine, Column, String, Text, Integer, DateTime, JSON, Boolean, ForeignKey, ARRAY, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime, timezone
import uuid

from app.config import settings

Base = declarative_base()

engine = create_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        try:
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT ''")
            )
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS last_summary_at TIMESTAMP")
            )
            conn.execute(
                text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_text TEXT DEFAULT ''")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS idx_chunks_search_text ON chunks "
                     "USING GIN (to_tsvector('simple', search_text))")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS idx_pii_alerts_status ON pii_alerts (status)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS idx_pii_hold_status ON pii_hold (status)")
            )
            conn.execute(
                text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64) DEFAULT ''")
            )
            conn.execute(
                text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64) DEFAULT ''")
            )
            conn.execute(
                text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP")
            )
            conn.execute(
                text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedded_chunk_count INTEGER DEFAULT 0")
            )
            conn.execute(
                text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_message VARCHAR(1024) DEFAULT ''")
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


from contextlib import contextmanager
from collections.abc import Generator


def get_session():
    return SessionLocal()


def get_db() -> Generator:
    """FastAPI Depends generator — yields session, auto-closes on request end."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def get_db_ctx():
    """Context manager for internal (non-route) use."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def utc_now():
    return datetime.now(timezone.utc)


# ── Auth ────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id = Column(String(64), primary_key=True, default=new_id)
    username = Column(String(64), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    display_name = Column(String(128), default="")
    email = Column(String(128), default="")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now)


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(String(256), default="")


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permission = Column(String(64), nullable=False)


# ── Knowledge Base ──────────────────────────────────────

class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id = Column(String(64), primary_key=True, default=new_id)
    name = Column(String(128), nullable=False)
    visibility = Column(String(16), nullable=False, default="public")  # public / internal / restricted
    owner_id = Column(String(64), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utc_now)


class KBRoleAccess(Base):
    __tablename__ = "kb_role_access"
    kb_id = Column(String(64), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


# ── Document ────────────────────────────────────────────

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(64), unique=True, nullable=False, index=True, default=new_id)
    kb_id = Column(String(64), ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    filename = Column(String(256), nullable=False)
    access_level = Column(String(16), default="inherit")  # inherit / public / internal
    owner_id = Column(String(64), ForeignKey("users.id"), nullable=False)
    status = Column(String(32), default="indexing")
    chunk_count = Column(Integer, default=0)
    embedded_chunk_count = Column(Integer, default=0)
    error_message = Column(String(1024), default="")
    content_hash = Column(String(64), default="")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, nullable=True)


class DocRoleAccess(Base):
    __tablename__ = "doc_role_access"
    document_id = Column(String(64), ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


# ── Chunk (with pgvector) ───────────────────────────────

class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(64), unique=True, nullable=False, index=True)
    document_id = Column(String(64), ForeignKey("documents.document_id"), nullable=False, index=True)
    kb_id = Column(String(64), ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    embedding = Column(Vector(4096))
    title = Column(String(256), default="")
    summary = Column(Text, default="")
    questions = Column(Text, default="")
    section_path = Column(String(512), default="")
    search_text = Column(Text, default="")
    content_hash = Column(String(64), default="")
    visibility = Column(String(16), default="public")
    allowed_roles = Column(ARRAY(Integer), default=list)
    created_at = Column(DateTime, default=utc_now)


# ── Conversation ────────────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(256), default="")
    summary = Column(Text, default="")
    last_summary_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(String(64), ForeignKey("conversations.conversation_id"), nullable=False, index=True)
    user_id = Column(String(64), ForeignKey("users.id"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utc_now)


# ── PII / Sensitive Data ─────────────────────────────────

class SensitiveRule(Base):
    __tablename__ = "sensitive_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_name = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=False)
    pattern = Column(String(512), nullable=False)
    validation_fn = Column(String(64), default="")
    strategy = Column(String(16), nullable=False, default="mask")  # mask / reject / audit
    mask_mode = Column(String(16), default="partial")  # partial / full
    exclusion_words = Column(String(1024), default="")
    description = Column(String(256), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class PiiAlert(Base):
    __tablename__ = "pii_alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(16), nullable=False)  # document / chat
    source_id = Column(String(64), nullable=False, index=True)
    rule_name = Column(String(64), nullable=False)
    matched_text = Column(Text, nullable=False)
    context_snippet = Column(Text, default="")
    strategy = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="pending")  # pending / confirmed / false_positive
    created_at = Column(DateTime, default=utc_now)
    resolved_at = Column(DateTime, nullable=True)


class PiiHold(Base):
    __tablename__ = "pii_hold"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(16), nullable=False)  # document / chat
    source_id = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending")  # pending / released / deleted
    created_at = Column(DateTime, default=utc_now)

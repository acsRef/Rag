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
        conn.execute(
            text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT ''")
        )
        conn.execute(
            text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS last_summary_at TIMESTAMP")
        )
        conn.commit()


def get_session():
    return SessionLocal()


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
    is_active = Column(Boolean, default=True)
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
    created_at = Column(DateTime, default=utc_now)


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
    visibility = Column(String(16), default="public")
    allowed_roles = Column(ARRAY(Integer), default=[])
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
    metadata_json = Column(JSON, default={})
    created_at = Column(DateTime, default=utc_now)

from sqlalchemy import create_engine, Column, String, Text, Integer, DateTime, JSON, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
import uuid

Base = declarative_base()


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True, default="default_user")
    title = Column(String(256), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(64), unique=True, nullable=False)
    conversation_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, default="default_user")
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, default={})
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DocumentRecord(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, default="default_user")
    kb_id = Column(String(64), nullable=False, default="default", index=True)
    filename = Column(String(256), nullable=False)
    status = Column(String(32), default="indexing")
    chunk_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


engine = create_engine("sqlite:///./ragent.db", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()


def new_id() -> str:
    return uuid.uuid4().hex[:16]

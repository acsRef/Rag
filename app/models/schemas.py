from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, Literal



# ── Auth ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=256, pattern=r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$|^$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    email: str
    is_active: bool
    role_ids: list[int] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    workspace_kb_id: str = ""


class ConversationResponse(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentListItem(BaseModel):
    document_id: str
    filename: str
    status: str
    kb_id: str
    chunk_count: int
    embedded_chunk_count: int = 0
    error_message: str = ""
    created_at: datetime | None = None


class UserRoleUpdateRequest(BaseModel):
    role_ids: list[int]


# ── Knowledge Base ──────────────────────────────────────

class KBCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    visibility: Literal["public", "internal", "restricted"] = "public"


class KBResponse(BaseModel):
    id: str
    name: str
    visibility: Literal["public", "internal", "restricted"]
    owner_id: str
    allowed_role_ids: list[int] = Field(default_factory=list)


class KBRoleAccessRequest(BaseModel):
    role_ids: list[int]


# ── Document ────────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    status: str
    chunk_count: int
    message: str = ""


class DocumentStatusResponse(BaseModel):
    document_id: str
    filename: str
    status: str
    chunk_count: int
    embedded_chunk_count: int = 0
    error_message: str = ""


# ── Chat ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    query: str = Field(min_length=1, max_length=4096)
    knowledge_base_ids: Optional[list[str]] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)


class ChatMessage(BaseModel):
    role: str
    content: str


# ── RAG internals ───────────────────────────────────────

class RewriteResult(BaseModel):
    rewritten_query: str
    sub_questions: list[str]


class IntentMatch(BaseModel):
    kb_id: str
    score: float


class IntentResult(BaseModel):
    sub_question: str
    matches: list[IntentMatch]
    intent_type: str


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str = ""
    text: str
    score: float
    title: str = ""
    summary: str = ""
    section_path: str = ""


class SourceInfo(BaseModel):
    chunk_id: str
    document_id: str
    filename: str = ""
    title: str = ""
    section_path: str = ""
    snippet: str = ""
    score: float = 0.0

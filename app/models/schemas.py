from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

_DATE_ENCODER = {datetime: lambda v: v.isoformat() if v else ""}


# ── Auth ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = ""
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


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
    role_ids: list[int] = []
    roles: list[str] = []
    permissions: list[str] = []


class ConversationResponse(BaseModel):
    model_config = {"json_encoders": _DATE_ENCODER}
    conversation_id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentListItem(BaseModel):
    model_config = {"json_encoders": _DATE_ENCODER}
    document_id: str
    filename: str
    status: str
    kb_id: str
    chunk_count: int
    created_at: datetime | None = None


class UserRoleUpdateRequest(BaseModel):
    role_ids: list[int]


# ── Knowledge Base ──────────────────────────────────────

class KBCreateRequest(BaseModel):
    name: str
    visibility: str = "public"


class KBResponse(BaseModel):
    id: str
    name: str
    visibility: str
    owner_id: str
    allowed_role_ids: list[int] = []


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


# ── Chat ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    user_id: str = "anonymous"
    conversation_id: Optional[str] = None
    query: str
    knowledge_base_ids: Optional[list[str]] = None
    temperature: float = 0.7
    top_p: float = 0.9


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

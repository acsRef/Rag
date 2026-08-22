from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── Auth ────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(
        default="", max_length=256, pattern=r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$|^$"
    )


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
    conversation_id: str | None = None
    query: str = Field(min_length=1, max_length=4096)
    knowledge_base_ids: list[str] | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)


class ChatMessage(BaseModel):
    role: str
    content: str


# ── RAG internals ───────────────────────────────────────


class RewriteResult(BaseModel):
    rewritten_query: str
    sub_questions: list[str]
    # 每个子问题的依赖关系（0-based 索引），如 [[], [0], [0,1]] 表示第2个子问题依赖第1个，第3个依赖前两个
    # 空列表表示无依赖。LLM 在 rewrite 时输出，但失败时安全降级
    sub_dependencies: list[list[int]] = Field(default_factory=list)


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
    # 显式年份元数据（从 document.filename 提取），用于时序追溯场景。
    # 不依赖路径猜测——空字符串表示该文档无年份（如用户手册/API 文档）。
    year: str = ""


class SourceInfo(BaseModel):
    chunk_id: str
    document_id: str
    filename: str = ""
    title: str = ""
    section_path: str = ""
    snippet: str = ""
    score: float = 0.0


# ── Retrieve（只检索不生成，供字典桥/外部消费） ────────────


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    kb_ids: list[str] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=50)


class RetrievedItem(BaseModel):
    """对外 /retrieve 线路契约；内部管线请用 RetrievedChunk。"""

    chunk_id: str
    document_id: str
    text: str
    title: str = ""
    section_path: str = ""
    score: float


class RetrieveResponse(BaseModel):
    items: list[RetrievedItem]
    degraded: bool = False

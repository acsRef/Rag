from pydantic import BaseModel, Field
from typing import Optional


class ChatRequest(BaseModel):
    user_id: str = Field(default="default_user")
    conversation_id: Optional[str] = None
    query: str
    knowledge_base_ids: Optional[list[str]] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    status: str
    chunk_count: int


class DocumentStatusResponse(BaseModel):
    document_id: str
    filename: str
    status: str
    chunk_count: int


class RewriteResult(BaseModel):
    rewritten_query: str
    sub_questions: list[str]


class IntentMatch(BaseModel):
    kb_id: str
    score: float


class IntentResult(BaseModel):
    sub_question: str
    matches: list[IntentMatch]
    intent_type: str  # KB / MCP / SYSTEM


class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    score: float
    kb_id: str
    document_id: str
    metadata: dict = {}

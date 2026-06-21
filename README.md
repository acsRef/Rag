# RAGent — RAG Document Processing & Q&A System

Document ingestion pipeline with parsing, cleaning, smart chunking, vector retrieval, RBAC auth, and a chat-style frontend.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python + FastAPI |
| Frontend | Vue 3 + Vite + TypeScript |
| Database | PostgreSQL 15 + pgvector 0.8 |
| Auth | JWT + bcrypt (RBAC, 7 permissions) |
| LLM | MiniMax M3 (chat + vision) |
| Embedding | Qwen3-VL-Embedding-8B (4096d) |
| Rerank | BAAI/bge-reranker-v2-m3 |

## Architecture

```
[Frontend :5173] → proxy → [FastAPI :8000]
                               ├── Auth API (register/login/me)
                               ├── Chat API (streaming SSE)
                               ├── Document API (upload/list)
                               ├── Knowledge Base API (CRUD)
                               └── Admin API (user management)

Ingestion Pipeline:
Upload → Parser → Cleaner → Structurer → Chunker → Metadata → Embed → pgvector
```

## Features

- **File parsing**: PDF/DOCX/PPTX/XLSX/HTML via Docling, images via MiniMax Vision, TXT direct decode
- **Smart chunking**: Structure-aware recursive split, atomic block protection (code/table/image), overlap
- **Long conversation**: Recent N turns + compressed summary for older history, pronoun resolution in query rewrite
- **RBAC**: 7 granular permissions (chat, doc.upload, doc.read_all, kb.create, kb.delete, kb.manage_visibility, user.manage, admin)
- **Knowledge Base**: public / internal / restricted visibility with role-based access control
- **Streaming chat**: Server-Sent Events, iMessage-style UI

## Quick Start

### 1. Database (PostgreSQL + pgvector)

```bash
docker compose up -d
```

### 2. Backend

```bash
conda activate rag  # or: pip install -r requirements.txt
python app/main.py
# → http://localhost:8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### 4. Login

Default admin: `admin` / `admin123`

## Project Structure

```
├── app/
│   ├── main.py                 # FastAPI entry
│   ├── config.py               # pydantic-settings
│   ├── api/                    # Route handlers
│   ├── core/                   # RAG pipeline, memory, rewrite
│   ├── ingestion/              # Parse → Clean → Structure → Chunk → Index
│   ├── llm/                    # Chat, embedding, rerank, vision clients
│   ├── middleware/auth.py      # JWT auth middleware
│   ├── models/schemas.py       # Pydantic schemas
│   └── store/                  # SQLAlchemy models, auth store, pgvector store
├── frontend/
│   └── src/                    # Vue 3 SPA
├── docker/
│   └── Dockerfile              # postgres:15 + pgvector build
├── docker-compose.yml
└── requirements.txt
```

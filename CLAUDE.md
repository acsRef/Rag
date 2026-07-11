# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First: read AGENTS.md

[AGENTS.md](AGENTS.md) is the primary AI-assistant guide. Read it before doing anything else. This file covers what AGENTS.md doesn't.

## Git LFS — critical

All `.py` files are Git LFS pointers. After a fresh clone, you **must** run:
```bash
git lfs install && git lfs pull
```
Without this, Python files read as binary garbage and imports fail.

## Start the app

```bash
# 1. Database
docker compose up -d

# 2. Backend (Python 3.11, conda env "rag" at D:\miniConda\envs\rag)
D:/miniConda/envs/rag/python.exe -m app.main
# Optional: LOG_LEVEL=DEBUG for retrieval diagnostics
# → http://localhost:8000

# 3. Frontend (separate terminal, in frontend/)
npm install        # first time or after package.json changes
npm run dev
# → http://localhost:5173
```

Default admin: `admin` / `admin123`

## Required environment variables

Edit `.env` in the project root (`.env` is gitignored — never commit it):

| Variable | Required | Notes |
| ------- | -------- | ----- |
| `MINIMAX_API_KEY` | Yes | Chat + Vision LLM |
| `SILICONFLOW_API_KEY` | Yes | Embedding + Rerank |
| `JWT_SECRET` | **Hard requirement** | Startup crashes if default |
| `PII_ENCRYPTION_KEY` | **Hard requirement** | Startup crashes if default |

## API routes

All under `/api/v1/`:

| Prefix | File | Notes |
| ------ | ---- | ----- |
| `/auth` | [app/api/auth.py](app/api/auth.py) | Register, login, profile |
| `/chat/stream` | [app/api/chat.py](app/api/chat.py) | SSE stream: `metadata`→`sources`→`token`→`done`/`error` |
| `/documents` | [app/api/documents.py](app/api/documents.py) | Upload (with incremental update), list, delete |
| `/kb` | [app/api/kb.py](app/api/kb.py) | Knowledge base CRUD |
| `/admin` | [app/api/admin.py](app/api/admin.py) | User management + PII audit (confirm/false-positive/whitelist) |

## Verify code changes

There is **no test framework**. The project intentionally has no pytest/tests directory. Verification:
```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"  # import chain check
```
For runtime verification, start the app and exercise the affected endpoint.

## Build frontend

```bash
cd frontend && npm run build   # runs vue-tsc -b && vite build
```

## Architecture snapshot

**Stack**: FastAPI (Python 3.11) + Vue 3/Vite/TypeScript + PostgreSQL 15 + pgvector 0.8

**LLM providers**: MiniMax M3 (chat/vision) + SiliconFlow (embedding Qwen3-VL-Embedding-8B 4096d + rerank BAAI/bge-reranker-v2-m3)

**RAG pipeline** (see [app/core/pipeline.py:86](app/core/pipeline.py#L86)):
```
QueryRewrite → IntentClassify (route to 1-3 KBs) → Hybrid Search (vector cosine + BM25 ts_rank, RRF merge)
→ Cross-encoder Rerank → MMR diversity (λ=0.7, ≤2 per doc) → TopK → Prompt injection → SSE stream
```

**Document ingestion** ([app/ingestion/](app/ingestion/)):
```
Parser → Cleaner → Structurer → Chunker → Metadata → Indexer (with incremental hash reuse + PII filtering)
```

**Database**: 14 tables, `chunks.embedding` (pgvector) + `chunks.search_text` (GIN tsvector), `init_db()` is idempotent (CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT EXISTS). Connection: `postgresql://ragent:ragent@localhost:5432/ragent`.

**Auth**: JWT + bcrypt, 8 RBAC permissions, 3-tier KB visibility (public/internal/restricted). Middleware at [app/middleware/auth.py](app/middleware/auth.py:56).

**PII detection** (3-layer, see [app/core/pii_scanner.py](app/core/pii_scanner.py)):

1. Regex (ID card, phone, email, bank card enabled by default; passport disabled)
2. Algorithm verification (Luhn, mod-11, phone carrier check)
3. Context exclusion (skip if "sample"/"test" in ±20 chars)

Strategies: `mask(partial)` (keep first 3/last 4), `mask(full)` → `[已脱敏]`, `reject` (block + alert), `audit` (alert only). Rejected docs go to `pii_hold` table for admin review.

## Supported file types for upload

`.pdf` `.docx` `.pptx` `.xlsx` `.html` `.txt` `.md` `.csv` `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp`

## Logging

- Config: [app/core/logging.py](app/core/logging.py)
- Output: `logs/ragent-YYYY-MM-DD.log`, 10 MB rotation, 7 backups
- Format: `timestamp [LEVEL][module]: message`
- Key log paths: `ingest.*`, `retrieve.*`, `embed.*`, `hybrid.*`

## Constraints / do not change

- Do **not** modify SQLAlchemy models in [app/store/db.py](app/store/db.py) (requires migrations)
- Do **not** introduce pytest or a `tests/` directory
- Do **not** add icon libraries to the frontend (emoji + inline SVG only)
- Do **not** remove the incremental hash reuse in [app/ingestion/indexer.py](app/ingestion/indexer.py)
- Do **not** modify the 5 default PII rules (adding new rules is OK)
- Do **not** take over uvicorn's logger
- Do **not** commit real API keys (`.env` is gitignored)
- Do **not** introduce `trace_id`/`contextvars` (project decision: no distributed tracing)

## Common pitfalls

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `No module named 'app'` | Windows: `python app/main.py` doesn't add cwd to sys.path | Use `python -m app.main` |
| `conda activate` still points to base | Shell didn't activate conda | Use absolute path `D:/miniConda/envs/rag/python.exe -m app.main` |
| `RuntimeError: 请设置 JWT_SECRET` | `.env` not configured | Edit `.env` → set `JWT_SECRET` and `PII_ENCRYPTION_KEY` |
| Startup stuck at `init_db` | PostgreSQL isn't running | `docker compose up -d` + `pg_isready` |
| Embedding 429 rate limit | SiliconFlow RPS exceeded | Lower `embedding_rate_limit_rps` (default 5) |
| Retrieval returns 0 results | KB has no indexed documents | Upload documents first |
| f-string `\n` SyntaxError | Python 3.11 f-expressions don't allow backslash | Use `chr(10)` or constant `_NL = '\\n'` |
| `X pipe None` TypeError | Non-type objects (e.g. `threading.Lock`) can't use `pipe` | Use `Optional[X]` instead |

## Quick verification

```bash
# Backend health
curl http://localhost:8000/health

# Login → get JWT token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'

# Import chain check (no test framework)
D:/miniConda/envs/rag/python.exe -c "import app.main"

# End-to-end test: upload docs from test-docs/ → RAG query → SSE stream
```

## Project structure

```
├── app/
│   ├── main.py               # FastAPI entry, startup sequence
│   ├── config.py              # pydantic-settings (all config in one place)
│   ├── api/                   # Route handlers (FastAPI APIRouter)
│   │   ├── chat.py            # SSE streaming chat endpoint
│   │   ├── documents.py       # Upload (incremental), list, delete
│   │   ├── kb.py              # Knowledge base CRUD
│   │   ├── auth.py            # Register, login, profile
│   │   ├── admin.py           # User management + PII audit
│   │   └── diagnostics.py     # RAG pipeline telemetry API
│   ├── core/                  # RAG pipeline core logic
│   │   ├── pipeline.py        # RAGPipeline.execute (main flow)
│   │   ├── retrieval.py       # Hybrid search + MMR
│   │   ├── mmr.py             # MMR diversity algorithm
│   │   ├── memory.py          # Token-budget conversation memory
│   │   ├── rewrite.py         # Query rewrite + anaphora resolution
│   │   ├── intent.py          # Intent classification for KB routing
│   │   ├── prompt.py          # Prompt assembly with token budgeting
│   │   ├── pii_scanner.py     # 3-layer PII detection
│   │   ├── pii_rules.py       # 5 default PII regex rules
│   │   └── diagnostics.py     # DiagContext recorder
│   ├── ingestion/             # Document processing pipeline
│   │   ├── parser.py          # Parse bytes → Markdown (multi-format)
│   │   ├── cleaner.py         # Text cleaning
│   │   ├── structurer.py      # Structure-aware chunking prep
│   │   ├── chunker.py         # Recursive text chunking
│   │   ├── metadata.py        # LLM-generated metadata
│   │   ├── indexer.py         # Indexing with hash reuse + PII filter
│   │   └── pipeline.py        # IngestionPipeline orchestrator
│   ├── llm/                   # Async LLM clients (OpenAI-compatible)
│   │   ├── base.py            # AsyncOpenAI wrapper + circuit breaker
│   │   ├── chat.py            # MiniMax M3 chat completions
│   │   ├── embedding.py       # SiliconFlow embeddings
│   │   ├── rerank.py          # Cross-encoder reranking
│   │   └── vision.py          # Image understanding (LRU cache)
│   ├── middleware/auth.py     # JWT + RBAC middleware
│   ├── models/schemas.py      # Pydantic request/response models
│   └── store/
│       ├── db.py              # SQLAlchemy models (do not modify)
│       ├── auth_store.py      # User/role/permission CRUD
│       └── pgvector_store.py  # Vector + BM25 hybrid search
├── frontend/
│   └── src/
│       ├── main.ts            # Vue 3 app entry
│       ├── api/               # Axios client + interceptors (auto Bearer)
│       ├── stores/            # Pinia stores (auth, chat, kb, …)
│       ├── router/            # Vue Router
│       ├── views/             # Page-level components
│       ├── components/        # Reusable UI components
│       └── styles/            # Global styles (Apple design language)
├── tools/
│   ├── diagnostics.html       # Standalone RAG pipeline viewer
│   ├── sample-detail.json     # (generated)
│   └── sample-index.json      # (generated)
├── test-docs/                 # End-to-end test documents (4 .md files)
├── docker/
│   └── Dockerfile             # postgres:15 + pgvector
├── docker-compose.yml
└── requirements.txt

## Diagnostics subsystem

Live RAG-pipeline telemetry, recorded per request and served to a standalone HTML viewer.

- **Recorder**: [app/core/diagnostics.py](app/core/diagnostics.py) — `DiagContext` accumulates per-step records (`rewrite`, `intent`, `retrieve`, `rerank`, `mmr`, `stream`, …) and writes one JSON per request under `diagnostics/YYYY-MM-DD/HHMMSS-<id>.json` + an `index.json`.
- **API**: [app/api/diagnostics.py](app/api/diagnostics.py) — `GET /api/v1/diag/index` and `/api/v1/diag/{id}` serve the recorded telemetry.
- **Viewer**: [tools/diagnostics.html](tools/diagnostics.html) — standalone page that fetches the diag API and renders the full pipeline chain. Open directly in a browser (no Vite build).

`pipeline.execute` constructs a `DiagContext`, records each stage, and calls `ctx.save()`; SSE stream metrics are back-filled via `ctx.update("stream", ...)` after streaming completes.

## Conversation memory

[app/core/memory.py](app/core/memory.py) — `ConversationMemory` implements token-budget dialog memory: keeps a recent message window, summarizes older turns with the LLM when the budget is exceeded, and persists both to the DB. Conversation-level lock via `threading.Lock` (note: `threading.Lock | None` is not a valid type — use `Optional[X]` there).

## LLM client layer

[app/llm/](app/llm/) — async LLM clients built on `AsyncOpenAI` ([base.py](app/llm/base.py), [chat.py](app/llm/chat.py), [embedding.py](app/llm/embedding.py), [rerank.py](app/llm/rerank.py), [vision.py](app/llm/vision.py)). All LLM I/O is async — never call these from sync code or block the event loop. Vision runs concurrently with chat via async tasks.

## Next up / pending work

| Task | Detail | Blocked by |
| ----- | ----- | ----------- |
| 搜索质量评估框架 | `eval/` 目录 + CLI 工具，见记忆文件 `search-quality-eval.md` | 文档 + 标注数据 |
| 异步文档处理 | Redis + ARQ 任务队列，大文件上传不阻塞 worker | 引入 Redis |
| 成本追踪 | JSONL 计费日志，`usage/YYYY-MM-DD.jsonl` | 低优先级 |

## Key file map

| Concern | Location |
| ------- | -------- |
| RAG main flow | [app/core/pipeline.py:86](app/core/pipeline.py#L86) `RAGPipeline.execute` |
| Hybrid search + RRF | [app/store/pgvector_store.py:197](app/store/pgvector_store.py#L197) `hybrid_search` |
| MMR algorithm | [app/core/mmr.py:25](app/core/mmr.py#L25) `mmr_select` |
| PII scanner (3-layer) | [app/core/pii_scanner.py:116](app/core/pii_scanner.py#L116) `scan` |
| Incremental hash reuse | [app/ingestion/indexer.py:100](app/ingestion/indexer.py#L100) `existing.content_hash == doc_hash` |
| Ingestion main flow | [app/ingestion/indexer.py:33](app/ingestion/indexer.py#L33) `DocumentIndexer.index` |
| Startup sequence | [app/main.py:44](app/main.py#L44) `startup` |
| JWT middleware | [app/middleware/auth.py:56](app/middleware/auth.py#L56) `get_current_user` |
| SSE stream endpoint | [app/api/chat.py:13](app/api/chat.py#L13) `stream_chat` |
| Diag recorder | [app/core/diagnostics.py](app/core/diagnostics.py) `DiagContext` |
| Conversation memory | [app/core/memory.py:68](app/core/memory.py#L68) `ConversationMemory` |
| Document parser | [app/ingestion/parser.py:47](app/ingestion/parser.py#L47) `parse_bytes` |
| Text chunker | [app/ingestion/chunker.py:51](app/ingestion/chunker.py#L51) `TextChunker.chunk` |
| Frontend SSE parser | [frontend/src/api/chat.ts:38](frontend/src/api/chat.ts#L38) `streamChat` |
| Config (all settings) | [app/config.py](app/config.py) |

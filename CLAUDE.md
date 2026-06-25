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

**RAG pipeline** (see [app/core/pipeline.py](app/core/pipeline.py:51)):
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

## Next up / pending work

| Task | Detail | Blocked by |
| ----- | ----- | ----------- |
| 搜索质量评估框架 | `eval/` 目录 + CLI 工具，见记忆文件 `search-quality-eval.md` | 文档 + 标注数据 |
| 异步文档处理 | Redis + ARQ 任务队列，大文件上传不阻塞 worker | 引入 Redis |
| 成本追踪 | JSONL 计费日志，`usage/YYYY-MM-DD.jsonl` | 低优先级 |
| 诊断 HTML 页面 | `diagnostics/html/index.html` 可视化 RAG 管线全链路 | 测试环境 |

## Key file map

| Concern | Location |
| ------- | -------- |
| RAG main flow | [app/core/pipeline.py:51](app/core/pipeline.py) |
| Hybrid search + RRF | [app/store/pgvector_store.py:199](app/store/pgvector_store.py) |
| MMR algorithm | [app/core/mmr.py:12](app/core/mmr.py) |
| PII scanner (3-layer) | [app/core/pii_scanner.py:102](app/core/pii_scanner.py) |
| Incremental hash reuse | [app/ingestion/indexer.py:108](app/ingestion/indexer.py) |
| Startup sequence | [app/main.py:31](app/main.py) |
| JWT middleware | [app/middleware/auth.py:56](app/middleware/auth.py) |
| SSE stream endpoint | [app/api/chat.py:12](app/api/chat.py) |
| Document parser | [app/ingestion/parser.py:35](app/ingestion/parser.py) |
| Text chunker | [app/ingestion/chunker.py:26](app/ingestion/chunker.py) |
| Frontend SSE parser | [frontend/src/api/chat.ts:34](frontend/src/api/chat.ts) |
| Config (all settings) | [app/config.py](app/config.py) |

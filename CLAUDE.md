# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First: read AGENTS.md

[AGENTS.md](AGENTS.md) is the primary AI-assistant guide. Read it before doing anything else. This file covers what AGENTS.md doesn't.

**Stale AGENTS.md items — this file wins:**
- "本仓库没有 pytest/tests/ 目录，不要创建" (§2/§6/§10) — outdated. `tests/` + `pytest.ini` now exist; see "Verify code changes" below.
- "Git LFS" (§9) — outdated and removed in Phase 1. No `.gitattributes`, no LFS-tracked files; `.py` files are plain text and no `git lfs pull` is needed after cloning.

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
| `SILICONFLOW_API_KEY` | **Yes** | Chat (Qwen3-8B) + Vision (Qwen3-VL-8B) + Intent (Qwen3-8B) + Complex-Query Rewrite (DeepSeek-R1-0528-Qwen3-8B) + Embedding + Rerank |
| `MINIMAX_API_KEY` | No | Optional fallback provider |
| `JWT_SECRET` | **Hard requirement** | Startup crashes if default |
| `PII_ENCRYPTION_KEY` | **Hard requirement** | Startup crashes if default |

## API routes

All under `/api/v1/`:

| Prefix | File | Notes |
| ------ | ---- | ----- |
| `/auth` | [app/api/auth.py](app/api/auth.py) | Register, login, profile |
| `/chat/stream` | [app/api/chat.py](app/api/chat.py) | SSE stream: `metadata`→`status`→`cross_doc`→`sources`→`thinking`/`token`→`degraded`→`done`/`error` (plus `no_context` when retrieval is empty) |
| `/documents` | [app/api/documents.py](app/api/documents.py) | Upload (with incremental update), list, delete |
| `/kb` | [app/api/kb.py](app/api/kb.py) | Knowledge base CRUD |
| `/admin` | [app/api/admin.py](app/api/admin.py) | User management + PII audit (confirm/false-positive/whitelist) |

## Verify code changes

Offline unit tests (never touch real DB / network / LLM — credentials are sentinelized by `tests/conftest.py`):

```bash
D:/miniConda/envs/rag/python.exe -m pytest                                   # full suite (unit + integration)
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q                     # offline unit only
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_mmr.py -v         # single file
D:/miniConda/envs/rag/python.exe -c "import app.main"                        # import chain check
```

Install dev deps first: `D:/miniConda/envs/rag/python.exe -m pip install -r requirements-dev.txt`.
Tests live in `tests/unit` (offline) and `tests/integration` (uses a separate `ragent_test` database on localhost PG, auto-skips when PG is unreachable; never writes to the dev `ragent` DB). Always use the **rag** conda env — never another project's env.
Known-bug-locking tests use `xfail(strict=False)` with a reason pointing at the fixing plan in `docs/plans/`.

Live real-API tests (`live_llm` marker, skipped unless enabled):

```bash
RAGENT_LIVE_LLM=1 D:/miniConda/envs/rag/python.exe -m pytest tests/integration -m live_llm -v
# 可选加速：RAGENT_LIVE_MODEL=deepseek-ai/DeepSeek-V3（仅文本对话；
# 图片理解固定走 settings.vision_model 的多模态模型，不受该开关影响）
```

需要 `.env` 真实 key；覆盖 16 轮长对话记忆不变式（有界滞后）、平安一季报 PDF 摄入检索、5 份复杂表格文档的跨文档检索。约 10 分钟。

For runtime verification, start the app and exercise the affected endpoint.

## Build frontend

```bash
cd frontend && npm run build   # runs vue-tsc -b && vite build
```

## Session workflow

**每次任务完成后，必须留下下一步计划**：

1. 在 `docs/plans/` 目录下创建新的计划文档（如 `YYYY-MM-DD-next-steps.md`）
2. 内容包括：
   - 本次完成的工作总结
   - 当前状态和分数（如果是优化任务）
   - 下一步具体行动计划
   - 优先级排序
3. 更新 `TODO.md`（如果存在）
4. 确保下一个会话可以直接从计划继续

**原因**：避免每次都要重新了解上下文和重复说明目标。

**示例**：
```markdown
# 下一步计划 (2026-08-20)

## 本次完成
- LLM 切换到 SiliconFlow (DeepSeek-V3)
- 检索层优化：权威表格补充检索
- A类: 56.7% → 83.3%, B类: 61.1% → 94.4%

## 下一步
1. H类改进（前提纠偏）- 2-3天
2. I类改进（拒答边界）- 1-2天
3. C类继续优化 - 2-3天

## 目标
- 整体分数从 71.8% 提升到 80%+
```

## Architecture snapshot

**Stack**: FastAPI (Python 3.11) + Vue 3/Vite/TypeScript + PostgreSQL 15 + pgvector 0.8

**LLM providers**: SiliconFlow (chat: Qwen/Qwen3-8B, intent: Qwen/Qwen3-8B, complex-query rewrite: DeepSeek-R1-0528-Qwen3-8B, vision: Qwen/Qwen3-VL-8B-Instruct, embedding: Qwen3-VL-Embedding-8B 4096d, rerank: BAAI/bge-reranker-v2-m3). MiniMax M3 available as fallback provider via `chat_provider="minimax"`.

**RAG pipeline** (see [app/core/pipeline.py:118](app/core/pipeline.py#L118) `RAGPipeline.execute`):
```
# Default runtime (strategy flags off unless noted; see app/config.py)
QueryRewrite → IntentClassify (DeepSeek-V3; route to 1-3 KBs) → Hybrid Search
(vector cosine + BM25 ts_rank + question-vector channel, RRF merge; relaxed-BM25
fallback when < top_k)
→ Cross-encoder Rerank → MMR diversity (λ=0.7, ≤2 per doc) → TopK
→ Prompt injection → SSE stream (TagStreamParser)

# Optional strategies (code exists, default OFF, env-togglable — 8-group ablation
# showed no recall gain on the Sany corpus, docs/plans/2026-08-23-ablation-report.md):
# cross_doc / section_boost / section_supplement / year_supplement /
# query_decomposition / evidence_gate
```
The question channel is on by default (`question_channel_enabled`, RRF weight 0.15): at ingest time the LLM generates candidate questions per chunk into `chunk_questions`, embedded separately. Streaming output passes through `TagStreamParser` ([app/core/tag_parser.py](app/core/tag_parser.py)) so SSE display and DB persistence share one think/answer event stream.

**Document ingestion** ([app/ingestion/](app/ingestion/)):
```
Parser → Cleaner → Structurer → Chunker → Metadata → Indexer (with incremental hash reuse + PII filtering)
→ Cross-doc relation matrix build (precomputes TF-IDF edges for cross-doc retrieval)
```

**Database**: 18 tables (incl. `chunk_questions` for the question channel and `doc_entities`/`doc_embeddings`/`doc_relations` for cross-doc), `chunks.embedding` (pgvector) + `chunks.search_text` (GIN tsvector), `init_db()` is idempotent (CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT EXISTS). Connection: `postgresql://ragent:ragent@localhost:5432/ragent`.

**Auth**: JWT + bcrypt, 8 RBAC permissions, 3-tier KB visibility (public/internal/restricted). Middleware at [app/middleware/auth.py:71](app/middleware/auth.py#L71).

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
- Tests live under `tests/` (pytest, see `pytest.ini`). New pure logic must ship offline unit tests; DB-dependent behavior uses `tests/integration` with the `ragent_test` database
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
| 跑测试误用 `agent` 环境 | 别的项目环境缺 numpy/pgvector/jieba 等依赖 | 一律 `D:/miniConda/envs/rag/python.exe -m pytest` |
| 担心 integration 测试污染开发库 | — | 不会：integration 只用自动创建的 `ragent_test` 库，fake 层不触真实 LLM；unit 层凭据全是哨兵值 |

## Quick verification

```bash
# Test suite (unit + integration, see "Verify code changes" above)
D:/miniConda/envs/rag/python.exe -m pytest -q

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
│   │   ├── retrieval.py       # Hybrid search + section-aware supplement + MMR
│   │   ├── evidence.py        # Evidence organization layer (sub-question tracking + conflict detection)
│   │   ├── mmr.py             # MMR diversity algorithm
│   │   ├── memory.py          # Token-budget conversation memory
│   │   ├── rewrite.py         # Query rewrite + anaphora resolution
│   │   ├── intent.py          # Intent classification for KB routing
│   │   ├── prompt.py          # Prompt assembly with token budgeting
│   │   ├── pii_scanner.py     # 3-layer PII detection
│   │   ├── pii_rules.py       # 5 default PII regex rules
│   │   ├── tag_parser.py      # TagStreamParser: think/answer tag parsing for the SSE stream (pure logic)
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
│   │   ├── chat.py            # SiliconFlow chat (DeepSeek-V3)
│   │   ├── embedding.py       # SiliconFlow embeddings
│   │   ├── rerank.py          # Cross-encoder reranking
│   │   └── vision.py          # Image understanding (Qwen3-VL-8B-Instruct, LRU cache)
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
│   └── diagnostics.html       # Standalone RAG pipeline viewer
├── test-docs/                 # End-to-end test documents (13 .md files; gitignored — local only, absent in fresh clones)
├── tests/
│   ├── conftest.py            # credential sentinel guard (unit never touches real services)
│   ├── unit/                  # offline unit tests
│   ├── integration/           # link tests on the ragent_test DB (never writes the dev DB / real LLM)
│   └── fixtures/docs/         # crafted cross-document fixture docs
├── eval/                      # Evaluation system
│   ├── eval_sany.py           # Full evaluation script (65 questions)
│   ├── eval_detail.py         # Detailed evaluation by category
│   ├── eval_single.py         # Single-question evaluation with retry
│   ├── rejudge.py             # Re-judge unanswered questions
│   └── sany_annual_reports/   # Sany annual reports evaluation dataset
├── docs/
│   ├── plans/                 # Implementation plans
│   └── TODO.md                # Task list
│   └── plans/                 # plan index + implementation plans (entry: docs/plans/README.md)
├── docker/
│   └── Dockerfile             # postgres:15 + pgvector
├── docker-compose.yml
├── requirements.txt
└── requirements-dev.txt       # test deps (pytest / pytest-asyncio)
```

## Cross-document relation retrieval

[app/core/doc_relation.py](app/core/doc_relation.py) — `cross_doc_retriever` enables jumping between related documents. Three channels:

1. **TF-IDF relation edges** — pre-built at ingest time, stored as a relation matrix per document.
2. **Query keyword recall** — uses the user query to find related docs by keyword overlap.
3. **Doc-level embedding** — semantic similarity between document-level vectors (not chunks).

Zero LLM/embedding cost at query time — all relations are precomputed during ingestion. Results are tagged with `cross_doc=True` and the source document for provenance.

Note: The most recent cross-doc design supersedes the earlier single-channel approach; see `app/core/retrieval.py` `_search_kb` for the integration point. Cross-doc retrieval is an opt-in strategy (see Optional Strategies above).

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
| RAG main flow | [app/core/pipeline.py:118](app/core/pipeline.py#L118) `RAGPipeline.execute` |
| Hybrid search + RRF | [app/store/pgvector_store.py:340](app/store/pgvector_store.py#L340) `hybrid_search` |
| Cross-doc relation | [app/core/doc_relation.py](app/core/doc_relation.py) `cross_doc_retriever` |
| MMR algorithm | [app/core/mmr.py:26](app/core/mmr.py#L26) `mmr_select` |
| PII scanner (3-layer) | [app/core/pii_scanner.py:134](app/core/pii_scanner.py#L134) `scan` |
| Incremental hash reuse | [app/ingestion/indexer.py:104](app/ingestion/indexer.py#L104) `existing.content_hash == doc_hash` |
| Ingestion main flow | [app/ingestion/indexer.py:35](app/ingestion/indexer.py#L35) `DocumentIndexer.index` |
| Startup sequence | [app/main.py:39](app/main.py#L39) `startup` |
| JWT middleware | [app/middleware/auth.py:71](app/middleware/auth.py#L71) `get_current_user` |
| SSE stream endpoint | [app/api/chat.py:13](app/api/chat.py#L13) `stream_chat` |
| SSE think/answer tag parser | [app/core/tag_parser.py](app/core/tag_parser.py) `TagStreamParser` |
| Diag recorder | [app/core/diagnostics.py](app/core/diagnostics.py) `DiagContext` |
| Conversation memory | [app/core/memory.py:83](app/core/memory.py#L83) `ConversationMemory` |
| Document parser | [app/ingestion/parser.py:47](app/ingestion/parser.py#L47) `parse_bytes` |
| Text chunker | [app/ingestion/chunker.py:85](app/ingestion/chunker.py#L85) `TextChunker.chunk` |
| Frontend SSE parser | [frontend/src/api/chat.ts:38](frontend/src/api/chat.ts#L38) `streamChat` |
| Config (all settings) | [app/config.py](app/config.py) |

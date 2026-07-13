# RAGent — Agent 工作指南

> **目标读者**:AI 助手(主要) + 人类开发者
> **用途**:本文件是项目的"系统提示",新对话开始时 AI 助手应**先读本文件再开始工作**

---

## 1. 项目概览

**RAGent-py** — 文档处理与智能问答系统。Python 3.11 FastAPI 后端 + Vue 3.5 前端 + PostgreSQL 15/pgvector 存储 + MiniMax M3 + SiliconFlow 双 LLM 栈。

核心能力:多格式文档解析 → 结构感知切块 → 向量化 + BM25 混合检索 → 跨编码器重排 → MMR 多样性 → 流式问答。PII 三层防御 + RBAC 8 权限 + 长对话摘要 + 增量 hash 复用。

---

## 2. Build / Lint / Test 命令

**本仓库没有 pytest,没有 tests/ 目录,不要创建。**

### 后端
```bash
# 启动数据库
docker compose up -d

# 启动后端(Python 3.11, conda env: rag)
D:/miniConda/envs/rag/python.exe -m app.main
LOG_LEVEL=DEBUG python -m app.main  # DEBUG 日志看检索细节

# 验证 import 链(替代测试)
D:/miniConda/envs/rag/python.exe -c "import app.main"
```

### 前端
```bash
# 启动(在 frontend/ 目录)
npm install   # 首次或 package.json 变更后
npm run dev   # → http://localhost:5173

# 构建(含 vue-tsc 类型检查)
npm run build
```

### 启动后验证
```bash
curl http://localhost:8000/health        # 后端
curl http://localhost:5173               # 前端
# 登录抓 token:
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

### 默认管理员
`admin` / `admin123`

---

## 3. 代码风格指南

### Python

**导入顺序**:stdlib → 第三方 → 本地(`from app.xxx`),用空行分隔组。禁用 `from module import *`。

**格式化**:4 空格缩进,行宽≈100。无 ruff/black/flake8 配置,保持与周围代码一致。

**类型标注**:优先 Python 3.10+ 的 `X | None` 和 `list[dict]` 语法(Python 3.11 支持良好)。注意 `threading.Lock | None` 等非 type 对象不能用 `|` — 改 `Optional[X]`。
```python
def search(kb_ids: list[str], top_k: int = 10) -> list[dict]: ...
async def stream() -> AsyncGenerator[str, None]: ...
```

**命名约定**:
- 类: `PascalCase` (`RAGPipeline`, `Settings`, `DocumentParser`)
- 函数/方法: `snake_case` (`get_current_user`, `mmr_select`, `_search_kb`)
- 常量: `UPPER_SNAKE_CASE` (`FILE_TYPE_MAP`, `_FMT`, `_NL`)
- 私有: 前导下划线 (`_build_sources`, `_rule_cache`)
- 模块级单例: 全小写 (`settings`, `rag_pipeline`, `minimax_client`)

**错误处理**:
- 自定义异常层次: `CircuitOpenError` → 熔断跳过; `PermanentError` → 不重试; `TemporaryError` → 退避重试
- `except:` 块内用 `logger.exception()` 记录完整 traceback
- 数据库 session: **必须**用 `try/finally` 保证 `session.close()`
  ```python
  session = get_session()
  try:
      rows = session.query(...).all()
      return rows
  finally:
      session.close()
  ```

**日志**:每个模块顶部 `logger = logging.getLogger(__name__)`,用结构化消息 `"action.key key=val key2=%s"`。

**配置**:统一通过 `app/config.py` 的 `Settings(BaseSettings)` 访问,`.env` 文件覆盖。

**API 路由**:`APIRouter(prefix="/api/v1/...", tags=[...])`,认证用 `Depends(get_current_user)`。

### TypeScript / Vue

**导入**:相对路径 `../stores/auth`,使用 `import type` 导入类型。

**命名**:变量/函数 camelCase,接口/类型 PascalCase (`User`, `ChatMessage`, `SourceInfo`)。

**Vue**:`<script setup lang="ts">` Composition API,避免 Options API。

**样式**:Apple 设计语言(`#007aff` accent),纯手写 CSS(**零图标库** — 只用 emoji + 内联 SVG)。

**API**:通过 `frontend/src/api/index.ts` 的 axios 实例,拦截器自动注 Bearer token,401 自动登出。

---

## 4. 架构快照

### RAG 管线
```
用户问题 → QueryRewrite(代词消解+子问题拆分)
  → 对每个子问题 IntentClassify(路由到1-3个KB)
  → Hybrid Search(向量余弦+BM25 ts_rank,RRF合并)
  → Cross-encoder Rerank → MMR多样性(λ=0.7,每文档≤2)
  → TopK(默认5) → Prompt注入 → LLM SSE流式输出
```

### 文档摄入
```
Parser(多格式→Markdown) → Cleaner → Structurer → Chunker
  → Metadata生成 → PII扫描(mask/reject) → Embedding
  → pgvector入库(增量hash复用:content_hash不变则跳过)
```

### 启动顺序
```
startup(): setup_logging() → JWT/PII_KEY校验(hard raise)
  → init_db()(幂等建表+迁移) → seed_defaults()(角色/权限/admin)
  → seed_pii_rules() → 恢复stuck文档 → RAGent-py startup complete
```

### LLM 栈
- **MiniMax M3**:对话 + 视觉(图片描述)
- **SiliconFlow**:Embedding `Qwen3-VL-Embedding-8B`(4096d) + Rerank `BAAI/bge-reranker-v2-m3`

### SSE 流式对话
`POST /api/v1/chat/stream` → 事件序列: `metadata`(conv_id) → `sources` → `token`(流) → `done`/`error`

---

## 5. 关键文件锚点

| 关注点 | 位置 |
|--------|------|
| RAG 主流程 | `app/core/pipeline.py:89` `RAGPipeline.execute` |
| 混合检索 RRF | `app/store/pgvector_store.py:196` `hybrid_search` |
| MMR 算法 | `app/core/mmr.py:25` `mmr_select` |
| PII 三层防御 | `app/core/pii_scanner.py:116` `scan` |
| 增量 hash 复用 | `app/ingestion/indexer.py:108` |
| JWT 中间件 | `app/middleware/auth.py:56` `get_current_user` |
| SSE 流式端点 | `app/api/chat.py:12` `stream_chat` |
| 摄取主流程 | `app/ingestion/indexer.py:29` `DocumentIndexer.index` |
| 文档解析 | `app/ingestion/parser.py:47` `DocumentParser.parse_bytes` |
| 前端 SSE 解析 | `frontend/src/api/chat.ts:38` `streamChat` |

---

## 6. 改动禁区

- ❌ 不要改 `app/store/db.py` 的**已有** SQLAlchemy 模型(需要数据库迁移)
  - ✅ 允许:在 db.py 底部加**新** model + `init_db()` 里加 `CREATE TABLE IF NOT EXISTS`(幂等模式)
  - ✅ 允许:在 `pgvector_store.py` 里加对应的 ORM 方法
- ❌ 不要接管 uvicorn 的 logger
- ❌ 不要去掉 `app/ingestion/indexer.py` 的增量 hash 复用
- ❌ 不要删/改 PII 5 条默认规则(可加新规则)
- ❌ 不要在前端引入 icon 库(emoji + 内联 SVG)
- ❌ 不要 commit 真实 API key(.env 不在 git 里)
- ❌ 不要创建 `tests/` 目录或 pytest 测试文件
- ❌ 不要引入 `trace_id`/`contextvars`(项目决策:不用全链路追踪)

---

## 7. 常见错误

| 症状 | 原因 | 解决 |
|------|------|------|
| `No module named 'app'` | Windows 下 `python app/main.py` 不加 cwd 到 sys.path | 用 `python -m app.main` |
| `conda activate` 后 python 仍指向 base | shell 没真正激活 conda | 用绝对路径 `D:/miniConda/envs/rag/python.exe -m app.main` |
| `RuntimeError: 请设置 JWT_SECRET` | .env 没配 | 改 `.env` 的 `JWT_SECRET`/`PII_ENCRYPTION_KEY` |
| 启动卡在 `init_db` | PostgreSQL 没起 | `docker compose up -d` + `pg_isready` |
| Embedding 429 限流 | SiliconFlow RPS 超限 | 调 `embedding_rate_limit_rps`(默认 5) |
| 检索 0 结果 | KB 没索引文档 | 先上传文档 |
| f-string `\n` SyntaxError | Python 3.11 的 f-string 表达式不支持反斜杠 | 用 `chr(10)` 或常量 `_NL = '\\n'` 替代 |
| `X | None` TypeError | 非 type 对象(如 `threading.Lock`)不能用 `|` | 改 `Optional[X]` |

---

## 9. Bug Fix History (2026-06-30)

**34 bugs fixed** across two rounds, touching 16+ files. All fixes verified via import chain and end-to-end test (upload 4 docs → RAG query → SSE stream complete).

### Round 1 — 33 bugs (bulk fix)
| 文件 | 修复 |
|------|------|
| `app/llm/rerank.py` | 4xx 不触发熔断(仅 5xx 调 `_on_failure`); 复用 httpx.AsyncClient |
| `app/core/retrieval.py` | Rerank 空数组不覆盖检索结果; `_collect_results` 用 `asyncio.to_thread` |
| `app/store/pgvector_store.py` | `replace_chunks` 原子事务; `get_neighbor_chunks` SQL 加 seq 范围过滤 |
| `app/ingestion/indexer.py` | 事务化 replace; PII 扫描缓存; Embedding 失败写 warning |
| `app/api/documents.py` | Upload 预检 file.size; SSE `/events` 401 正确返回 |
| `app/llm/embedding.py` | 15s 超时; RateLimiter 加 `asyncio.Lock`; embed 加重试 |
| `app/llm/base.py` | `_JSON_FENCE_PATTERN` 匹配嵌套 JSON; HALF_OPEN 单探测 |
| `app/core/memory.py` | DB 操作 `asyncio.to_thread`; 锁清理; DetachedInstanceError 防护 |
| `app/core/diagnostics.py` | 线程安全文件写入 |
| `app/core/pipeline.py` | ctx 遮蔽修复; 子问题并行 `asyncio.gather`; 进度事件; SSE \r 移除 |
| `app/api/kb.py` | restricted KB 列表修复; delete_kb 级联清理 DocRoleAccess+KBRoleAccess |
| `app/core/prompt.py` | 预算 ≤ 0 不保留 chunk; `_trim_history` 历史裁剪 |
| `app/llm/chat.py` | 懒加载 client 按 event loop 重建; `async with response` |
| `app/llm/vision.py` | 失败响应不写入缓存 |
| `app/core/pii_scanner.py` | `_has_exclusion` substring → word boundary regex |

### Round 2 — 7 missed fixes
| 问题 | 文件 | 修复 |
|------|------|------|
| `embed_batch` 死代码 | `embedding.py` | 删除整个方法 |
| 4xx 仍计入熔断 | `chat.py` (流式+同步), `embedding.py` | 先 `classify_llm_error(e)`, `isinstance(typed, PermanentError)` 则不调 `_on_failure()` |
| KB owner 看不到自己 restricted KB | `api/kb.py` | 加 `or_(public_cond, owner_cond)` 到查询 |
| `get_neighbor_chunks` SQL 全量查 | `store/pgvector_store.py` | `CAST(SUBSTRING(chunk_id FROM '_(\\\\d+)$') AS INTEGER) BETWEEN` 数值过滤 |
| `vision._cache` 无淘汰 | `llm/vision.py` | `OrderedDict` + `max_cache=1000`, LRU 淘汰 |
| PII 三重扫描 | `core/pii_scanner.py` + `ingestion/indexer.py` | `mask_text()` 加可选 `findings` 参数; indexer 复用缓存 |

### Architecture Notes (from fixes)
- **Circuit breaker 4xx rule**: ONLY 5xx/timeout/connection errors call `_on_failure()`. 4xx (auth, bad request, quota) must NOT trip breaker. Pattern: `classify_llm_error(e)` → `isinstance(typed, PermanentError)` → skip `_on_failure()`.
- **embed_batch** is dead code (never called from anywhere in codebase), deleted.
- **PII triple scan**: `mask_text()` now accepts optional `findings` param. Callers that already scanned should pass findings to avoid a 3rd scan pass.
- **Vision cache**: LRU via `OrderedDict`, max_cache=1000.
- **KB visibility**: Owner always sees their own KBs regardless of visibility setting.
- **get_neighbor_chunks**: Uses PostgreSQL `CAST(SUBSTRING(...) AS INTEGER) BETWEEN` for accurate numerical seq filtering (string BETWEEN breaks at seq≥10).

### End-to-end test docs
Located in `test-docs/`:
- `01-产品规格书_M3工业网关.md` — 10 chunks
- `02-2026年销售策略与渠道政策.md` — 14 chunks
- `03-员工手册_2026修订版.md` — 16 chunks
- `04-2025年Q4经营分析报告.md` — 11 chunks

Test KB ID: `a18e62187f234e7d` (from last session, may need recreation).

---

## 10. Git LFS

所有 `.py` 源文件通过 Git LFS 存储。克隆后:
```bash
git lfs install
git lfs pull
```
否则 `.py` 是 LFS 指针文件,import 会失败。

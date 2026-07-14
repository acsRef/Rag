# RAGent — Agent 工作指南

> **目标读者**:AI 助手(主要) + 人类开发者
> **用途**:新对话开始时 AI 助手应**先读本文件再开始工作**。CLAUDE.md 是本文件的补充(启动步骤、API 路由清单、环境变量、项目结构树)。

---

## 1. 项目概览

**RAGent-py** — 文档处理与智能问答系统。Python 3.11 FastAPI 后端 + Vue 3.5 前端 + PostgreSQL 15/pgvector 存储 + MiniMax M3 + SiliconFlow 双 LLM 栈。

核心能力:多格式文档解析 → 结构感知切块 → 向量化 + BM25 混合检索 → 跨编码器重排 → MMR 多样性 → 流式问答。PII 三层防御 + RBAC 8 权限 + 长对话摘要 + 增量 hash 复用 + 跨文档关联检索(三通道)。

---

## 2. Build / Lint / Test 命令

**本仓库没有 pytest/tests/ 目录,不要创建。**

```bash
# 启动数据库
docker compose up -d

# 启动后端(Python 3.11, conda env: rag)
D:/miniConda/envs/rag/python.exe -m app.main
LOG_LEVEL=DEBUG python -m app.main  # DEBUG 日志看检索细节

# 验证 import 链(替代测试)
D:/miniConda/envs/rag/python.exe -c "import app.main"

# 前端(在 frontend/ 目录)
npm install && npm run dev   # → http://localhost:5173
npm run build                # vue-tsc + vite build(类型检查)

# 启动后验证
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

默认管理员: `admin` / `admin123`

---

## 3. 代码风格指南

### Python

**导入顺序**:stdlib → 第三方 → 本地(`from app.xxx`),用空行分隔组。禁用 `from module import *`。

```python
import asyncio, json, logging, time          # stdlib
from dataclasses import dataclass
from enum import Enum

from fastapi import APIRouter, Depends       # third-party
from openai import AsyncOpenAI

from app.config import settings              # local
from app.store.db import get_session
```

**格式化**:4 空格缩进,行宽≈100。无 ruff/black/flake8 配置,**保持与周围代码一致**。

**类型标注**:优先 Python 3.10+ 语法(`X | None`, `list[dict]`)。注意 `threading.Lock | None` 等非 type 对象不能用 `|` — 改 `Optional[X]`。函数签名加返回类型:

```python
def search(kb_ids: list[str], top_k: int = 10) -> list[dict]: ...
async def stream() -> AsyncGenerator[str, None]: ...
```

**命名约定**:
- 类: `PascalCase` (`RAGPipeline`, `Settings`, `DocumentParser`, `CircuitBreaker`)
- 函数/方法: `snake_case` (`get_current_user`, `mmr_select`, `_search_kb`, `create_access_token`)
- 常量: `UPPER_SNAKE_CASE` (`FILE_TYPE_MAP`, `_FMT`, `_NL`, `_SUMMARY_FRESH`)
- 私有: 前导下划线 (`_build_sources`, `_rule_cache`, `_check_breaker`, `_get_admin_role_id`)
- 模块级单例: 全小写 (`settings`, `rag_pipeline`, `minimax_client`, `conversation_memory`)
- 布尔字段/变量: `is_active`, `has_permission`, `can_read_all`, `pii_enabled`

**错误处理**:
- 自定义异常: `CircuitOpenError` → 熔断跳过; `PermanentError`(4xx) → 不重试不触发熔断; `TemporaryError`(5xx/超时) → 退避重试+熔断计数
- `except:` 块内用 `logger.exception()` 记录完整 traceback
- 数据库 session: **必须**用 `try/finally` 或 `get_db_ctx()` 上下文管理器保证 close
  ```python
  session = get_session()
  try:
      return session.query(...).all()
  finally:
      session.close()
  ```

**日志**:每个模块顶部 `logger = logging.getLogger(__name__)`,结构化消息。

**配置**:统一通过 `app/config.py` 的 `Settings(BaseSettings)` 访问,`.env` 文件覆盖。所有可调参数在同一文件,禁止散落魔数。

**API 路由**:`APIRouter(prefix="/api/v1/...", tags=[...])`,认证用 `Depends(get_current_user)`。

**数据库会话**:优先 `with get_db_ctx() as session:` 上下文管理器,避免手动 close。所有 DB 操作尽量放工具函数,不要在路由 handler 里写 SQL。

### TypeScript / Vue

**导入**:相对路径 `../stores/auth`;类型导入用 `import type`。

```typescript
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type User } from '../api/auth'
```

**命名**:变量/函数 `camelCase`,接口/类型 `PascalCase` (`User`, `ChatMessage`, `SourceInfo`)。

**Vue**:`<script setup lang="ts">` Composition API(Pinia `defineStore` + 组合式写法),避免 Options API。

**样式**:Apple 设计语言(`#007aff` accent),纯手写 CSS。**零图标库** — 只用 emoji + 内联 SVG。

**API**:通过 `frontend/src/api/index.ts` 的 axios 实例,拦截器自动注 Bearer token,401 自动清 token 并跳转 `/login`。

---

## 4. 架构快照

### 启动顺序
`startup()`: `setup_logging()` → JWT/PII_KEY 校验 → `init_db()`(幂等建表+迁移) → `seed_defaults()`(角色/权限/admin) → `seed_pii_rules()` → 恢复 stuck 文档 → complete

### RAG 管线
```
QueryRewrite(代词消解+子问题拆分) → IntentClassify(路由到1-3个KB)
  → Hybrid Search(向量余弦+BM25 ts_rank,RRF合并)
  → Cross-doc Relation(三通道:TF-IDF边/关键词召回/文档级embedding)
  → Cross-encoder Rerank → MMR多样性(λ=0.7,每文档≤2)
  → TopK(默认5) → Prompt注入(token预算裁剪) → LLM SSE流式输出
```

### 文档摄入
```
Parser(多格式→Markdown) → Cleaner → Structurer → Chunker
  → Metadata生成 → PII扫描(mask/reject) → Embedding
  → pgvector入库(增量hash复用:content_hash不变则跳过)
  → Cross-doc关系矩阵预构建(TF-IDF边)
```

### LLM 栈
- **MiniMax M3**:对话 + 视觉(图片描述)。AsyncOpenAI 兼容接口,支持 SSE streaming
- **SiliconFlow**:Embedding `Qwen3-VL-Embedding-8B`(4096d) + Rerank `BAAI/bge-reranker-v2-m3`
- **熔断器**:按 provider 隔离,5xx/超时/连接错才计失败,4xx 永久错误不计

### SSE 流式对话
`POST /api/v1/chat/stream` → 事件序列: `metadata`(conv_id) → `sources` → `thinking`(思考过程) → `token`(生成流) → `done`/`error`

### 关键子系统
- **Conversation Memory**(`app/core/memory.py`): Token 预算制窗口 + 自动摘要压缩,超出预算按 chunks→history→summary 倒序裁剪
- **Diagnostics**(`app/core/diagnostics.py`): 每次 RAG 查询生成 JSON 诊断记录,写入 `diagnostics/` 目录,配套独立 HTML 查看器(`tools/diagnostics.html`)
- **PII 三层防御**(`app/core/pii_scanner.py`): 正则检测 → 算法校验(Luhn/mod-11) → 上下文排除(±20字 "sample"/"test"),策略: mask/reject/audit

---

## 5. 关键文件锚点

| 关注点 | 位置 |
|--------|------|
| RAG 主流程 | `app/core/pipeline.py:89` `RAGPipeline.execute` |
| 混合检索 RRF | `app/store/pgvector_store.py:196` `hybrid_search` |
| 跨文档关联检索 | `app/core/doc_relation.py` `cross_doc_retriever` |
| MMR 算法 | `app/core/mmr.py:25` `mmr_select` |
| PII 三层防御 | `app/core/pii_scanner.py:116` `scan` |
| 增量 hash 复用 | `app/ingestion/indexer.py:100` |
| JWT 中间件 | `app/middleware/auth.py:56` `get_current_user` |
| SSE 流式端点 | `app/api/chat.py:12` `stream_chat` |
| 摄取主流程 | `app/ingestion/indexer.py:29` `DocumentIndexer.index` |
| 文档解析 | `app/ingestion/parser.py:47` `DocumentParser.parse_bytes` |
| 对话记忆管理 | `app/core/memory.py:69` `ConversationMemory` |
| 诊断记录器 | `app/core/diagnostics.py` `DiagContext` |
| 前端 SSE 解析 | `frontend/src/api/chat.ts:38` `streamChat` |
| 配置中心 | `app/config.py` `Settings` |

---

## 6. 改动禁区

- ❌ 不要改 `app/store/db.py` 的**已有** SQLAlchemy 模型(需要数据库迁移)。✅ 允许:在 db.py 底部加**新** model + `init_db()` 里加 `CREATE TABLE IF NOT EXISTS`(幂等模式)
- ❌ 不要接管 uvicorn 的 logger
- ❌ 不要去掉 `app/ingestion/indexer.py` 的增量 hash 复用
- ❌ 不要删/改 PII 5 条默认规则(可加新规则)
- ❌ 不要在前端引入 icon 库(emoji + 内联 SVG)
- ❌ 不要 commit 真实 API key(`.env` 在 gitignore 中)
- ❌ 不要创建 `tests/` 目录或 pytest 测试文件
- ❌ 不要引入 `trace_id`/`contextvars`(项目决策:不用全链路追踪)
- ❌ 不要阻塞事件循环 — 所有 LLM I/O 是 async,DB 操作需用 `asyncio.to_thread`

---

## 7. 常见错误

| 症状 | 原因 | 解决 |
|------|------|------|
| `No module named 'app'` | Windows `python app/main.py` 不加 cwd 到 sys.path | 用 `python -m app.main` |
| `conda activate` 后 python 仍指向 base | shell 没真正激活 conda | 用绝对路径 `D:/miniConda/envs/rag/python.exe` |
| `RuntimeError: 请设置 JWT_SECRET` | `.env` 没配 | 改 `JWT_SECRET`/`PII_ENCRYPTION_KEY` |
| 启动卡在 `init_db` | PostgreSQL 没起 | `docker compose up -d` + `pg_isready` |
| Embedding 429 限流 | SiliconFlow RPS 超限 | 调 `embedding_rate_limit_rps`(默认 5) |
| 检索 0 结果 | KB 没索引文档 | 先上传文档 |
| f-string `\n` SyntaxError | Python 3.11 不支持 f-string 内反斜杠 | 用 `chr(10)` 或常量 `_NL = '\\n'` |
| `X \| None` TypeError | 非 type 对象(如 `threading.Lock`)不能用 `\|` | 改 `Optional[X]` |

---

## 8. 熔断器 4xx 规则(重要)

Only 5xx/timeout/connection errors call `_on_failure()`. 4xx (auth, bad request, quota) must NOT trip breaker.

```python
typed = classify_llm_error(e)
if isinstance(typed, PermanentError):
    # don't call _on_failure() — 4xx is permanent
    raise typed
# TemporaryError → retry + _on_failure()
```

---

## 9. Git LFS

所有 `.py` 源文件通过 Git LFS 存储。克隆后: `git lfs install && git lfs pull`。否则 `.py` 是 LFS 指针文件,import 会失败。

---

## 10. 关键设计决策

| 决策 | 理由 |
|------|------|
| 不用全链路追踪(trace_id/contextvars) | 复杂度增加 > 收益,诊断已够用 |
| 无 pytest/tests 目录 | import chain + 端到端手动验证替代 |
| 增量 hash 复用 | 避免重复 embedding,大幅降低 SiliconFlow 调用量 |
| PII 三层:正则有->算法验->上下文排除 | 减少误报,支持 "sample/test" 内容白名单 |
| LLM 懒加载按 event loop 重建 | Windows + uvicorn reload 场景避免事件循环错乱 |

# RAGent — Agent 工作指南

> **目标读者**:AI 助手(主要) + 人类开发者(兼顾)
> **用途**:本文件是项目的"系统提示",新对话开始时 AI 助手应**先读本文件再开始工作**
> **更新约定**:任何对启动方式、模块边界、禁区、日志格式的改动都要同步更新本文件

---

## 1. 项目概览

**RAGent-py** — 文档处理与智能问答系统。Python 3.11 FastAPI 后端 + Vue 3 前端 + PostgreSQL 15/pgvector 存储 + MiniMax M3 + SiliconFlow 双 LLM 栈。

- **核心能力**:多格式文档解析 → 结构感知切块 → 向量化 + BM25 混合检索 → 跨编码器重排 → MMR 多样性 → 流式问答
- **特殊能力**:PII 三层防御 + RBAC 8 权限 + 长对话摘要 + 增量 hash 复用

---

## 2. 快速启动

### 2.1 环境要求
- Python 3.11 (conda env `rag`,路径 `D:\miniConda\envs\rag`)
- Node.js(前端)
- Docker(数据库)

### 2.2 启动顺序

**第 1 步:数据库**(在项目根 `D:\PyProject\ragent-py`)
```bash
docker compose up -d
# 验证: docker exec ragent-postgres pg_isready -U ragent
```

**第 2 步:后端**(在项目根 `D:\PyProject\ragent-py`)
```bash
# 本项目的 Python 环境是 D:\miniConda\envs\rag(conda env 名: rag)
# Windows 推荐用绝对路径,绕过 conda activate 在某些终端不生效的问题:
D:/miniConda/envs/rag/python.exe -m app.main

# 等价写法(如果 conda activate 在当前 shell 生效):
conda activate rag
python -m app.main

# → http://localhost:8000
# 健康检查: curl http://localhost:8000/health
```

**第 3 步:前端**(在 `D:\PyProject\ragent-py\frontend`,另开终端)
```bash
npm install   # 首次或 package.json 变了才需要
npm run dev
# → http://localhost:5173
```

**默认管理员**:`admin` / `admin123`

### 2.3 .env 必填项
- `MINIMAX_API_KEY` / `SILICONFLOW_API_KEY` — LLM 服务 key
- `JWT_SECRET` / `PII_ENCRYPTION_KEY` — **不能用默认值**,启动会硬性 raise
- 其他都有默认值,可不改

---

## 3. Git LFS

所有 `.py` 源文件通过 Git LFS 存储。克隆后:
```bash
git lfs install
git lfs pull
```
**否则本地 `.py` 是 LFS 指针,Python import 会失败**(表现为奇怪错误或空文件)。

---

## 4. 项目结构(模块地图)

```
app/
├── main.py                # FastAPI 入口,startup 调 setup_logging + init_db + seed_*
├── config.py              # pydantic-settings,.env 覆盖
├── api/                   # 路由:auth, admin, chat(SSE), documents, kb
├── core/
│   ├── pipeline.py        # RAG 主流程
│   ├── retrieval.py       # 混合检索 + rerank + MMR
│   ├── mmr.py             # MMR 算法(归一化 + 跨文档软惩罚)
│   ├── memory.py          # 长对话 + 摘要压缩
│   ├── rewrite.py         # 代词消解 + 子问题拆分
│   ├── intent.py          # KB 路由意图分类
│   ├── prompt.py          # RAG prompt 模板
│   ├── pii_scanner.py     # PII 三层防御引擎
│   ├── pii_rules.py       # 5 条内置规则 + 校验函数
│   └── logging.py         # 日志基础设施(2026-06 新增)
├── ingestion/             # 文档处理:parser → cleaner → structurer → chunker → metadata → indexer
├── llm/                   # LLM 客户端:chat, embedding, rerank, vision
├── middleware/auth.py     # JWT 认证
├── models/schemas.py      # Pydantic schema
└── store/                 # 数据访问层
    ├── db.py              # SQLAlchemy 模型 + init_db 迁移
    ├── auth_store.py      # 用户/角色/权限 CRUD
    └── pgvector_store.py  # 混合检索 SQL + RRF 合并
```

---

## 5. 优先阅读路径(给 AI 助手)

如果只能读 3 个文件就理解全貌:
1. **`app/core/pipeline.py`** — RAG 主流程,5 个步骤如何串起来
2. **`app/core/retrieval.py`** — 检索核心,hybrid + rerank + MMR 三阶段
3. **`app/store/pgvector_store.py`** — 数据库层,权限过滤 SQL + RRF

次要(按需):
- 摄取管线:`app/ingestion/indexer.py`
- PII 引擎:`app/core/pii_scanner.py`
- 数据库模型:`app/store/db.py`

---

## 6. 关键技术细节

### 6.1 数据库
- PostgreSQL 15 + pgvector 0.8
- 连接串:`postgresql://ragent:ragent@localhost:5432/ragent`
- 14 张表(用户/角色/权限/知识库/文档/chunks/对话/消息/PII 规则/告警/暂存)
- 启动 `init_db()` 会建表 + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 幂等迁移
- 关键索引:`chunks.embedding` (pgvector)、`chunks.search_text` (GIN + tsvector)

### 6.2 外部 API
- **MiniMax M3**(`minimax_base_url`):对话 + 视觉(图片描述)
- **SiliconFlow**(`siliconflow_base_url`):Embedding `Qwen/Qwen3-VL-Embedding-8B`(4096d) + Rerank `BAAI/bge-reranker-v2-m3`

### 6.3 SSE 流式对话
- 端点:`POST /api/v1/chat/stream`
- 事件类型:`metadata`(conversation_id) → `sources` → `token`(流) → `done` / `error`
- 前端 `frontend/src/api/chat.ts:48-100` 手工解析

### 6.4 检索管线
```
用户问题 → QueryRewrite(代词消解 + 子问题拆分)
       → 对每个子问题 IntentClassify(路由到 1-3 个 KB)
       → Hybrid Search:向量余弦 + BM25 ts_rank,RRF 合并
       → Cross-encoder Rerank(精排)
       → MMR 多样性(λ=0.7,每文档≤2)
       → TopK(默认 5)→ Prompt 注入 → LLM 流式输出
```

### 6.5 启动钩子顺序
```
startup():
  setup_logging()        # 最早,连日志都没起也能记录
  → RAGent-py starting up
  → JWT_SECRET / PII_ENCRYPTION_KEY 硬校验(失败直接 raise)
  → init_db()           # 建表 + 迁移
  → seed_defaults()     # 角色/权限/默认 admin
  → seed_pii_rules()    # PII 规则同步到 DB
  → RAGent-py startup complete
```

---

## 7. 日志系统(2026-06 新增)

### 7.1 配置中心
- 文件:`app/core/logging.py`
- 入口:`setup_logging()`,`app/main.py` startup 调一次

### 7.2 输出位置
- 目录:`logs/`(项目根)
- 文件名:`ragent-YYYY-MM-DD.log`
- 单文件 10MB 切 `.1/.2/...`,保留 7 个 backup
- **同一天内**按大小切,**跨天**自动写新文件

### 7.3 格式
```
时间 [LEVEL][module]: 消息
例:2026-06-24 20:30:15 [INFO][app.ingestion.indexer]: ingest.chunked sections=5 chunks=42 elapsed_ms=85.3
```

### 7.4 切档调试
```bash
# 默认 INFO,临时切 DEBUG 看检索每步细节
LOG_LEVEL=DEBUG python -m app.main
```

### 7.5 关键路径日志
- **切块**(`app/ingestion/indexer.py`):6 个 `ingest.*` INFO
  - `ingest.start` / `ingest.parsed_cleaned` / `ingest.chunked` / `ingest.pii_masked`(DEBUG) / `ingest.reuse_matched` / `ingest.persisted`
- **检索**(`app/core/retrieval.py`):5 个 `retrieve.*`(3 INFO + 2 DEBUG)
  - `retrieve.start` / `retrieve.embedded`(DEBUG) / `retrieve.candidates` / `retrieve.reranked`(DEBUG) / `retrieve.final`
- **Embedding**:`embed.batch.ok` / `embed.fallback.done` INFO
- **混合检索**:`hybrid.search.done` INFO + `vector.search.start/done` DEBUG

---

## 8. PII 引擎

### 8.1 三层防御
1. 正则匹配(身份证/手机/邮箱/银行卡 4 种默认启用,护照默认禁用)
2. 算法校验(身份证 mod 11 / 手机号段位 / 银行卡 Luhn)
3. 上下文排除(前后 20 字符内出现"示例/test/sample"则跳过)

### 8.2 策略
- `mask(partial)`:保留首 3 末 4,中间 `*`
- `mask(full)`:整段替换 `[已脱敏]`
- `reject`:不入索引,告警入库
- `audit`:只告警(预留)

### 8.3 审核流
- 拒收文档入 `pii_hold` 表(暂存原内容)
- 告警入 `pii_alerts` 表
- 管理员通过 `/api/v1/admin/pii-alerts/*` 处理(确认 / 误报 / 白名单)

---

## 9. RBAC / 权限模型

- **8 项权限**:`chat`, `doc.upload`, `doc.delete`, `doc.read_all`, `kb.create`, `kb.delete`, `kb.manage_visibility`, `user.manage`, `admin`
- **3 级 KB 可见性**:`public` / `internal` / `restricted`
- 启动种子:`admin` 角色(9 项权限含 admin)+ `user` 角色(`chat` / `doc.upload` / `doc.delete`)
- 权限传递链:user → user_roles → roles → role_permissions
- 中间件:`app/middleware/auth.py:56` `get_current_user` 注入 route
- 路由级检查:`require_admin()` / `"doc.upload" in current_user["permissions"]`

---

## 10. 前端约束

- Vue 3.5 + Vite 6 + TS 5.7 + Pinia 2.3 + Vue Router 4.5
- 样式:**纯手写**,Apple 设计语言(`#007aff` accent / SF/PingFang 字体栈 / 毛玻璃 / **零图标库**,emoji + 内联 SVG)
- `npm run build` 含 `vue-tsc -b && vite build`,**无运行时类型检查**
- API 客户端:`frontend/src/api/`,axios 拦截器注 Bearer,401 自动登出
- 状态管理:Pinia `frontend/src/stores/auth.ts`,localStorage 持久化

---

## 11. 已删除清单(2026-06 清理)

**以下目录/文件不存在,AI 助手不要找/不要尝试 import**:
- `test/` — 旧手工测试脚本
- `test-docs/` — 上传测试样本
- `app/vector/` — 早期占位空包
- `_check_docs.py` — 临时调试脚本(已不在项目根)
- `.agents/skills/` — 空目录
- `frontend/dist/` — build 产物(已 .gitignore)
- `frontend/vite.config.js` — TS 版本(`.ts`)才是 Vite 实际读取的

---

## 12. 改动禁区(避免 AI 误改)

- ❌ 不要改 `app/store/db.py` 的 SQLAlchemy 模型(改动需要数据库迁移)
- ❌ 不要接管 uvicorn 的 logger(Q10 决策,保留 uvicorn 原状)
- ❌ 不要引入 trace_id / contextvars(Q7 用户选 B,只要 3-5 字段)
- ❌ 不要去掉 `app/ingestion/indexer.py` 的增量 hash 复用(性能优化核心)
- ❌ 不要删/改 PII 5 条默认规则(可加新规则,不要动默认)
- ❌ 不要在前端引入 icon 库(项目用 emoji + 内联 SVG)
- ❌ 不要 commit 真实 API key(.env 不该在 git 里)

---

## 13. 无测试框架

- ❌ **没有 pytest**,`test/` 目录已删
- ❌ 不要创建 `tests/` 目录
- ❌ 不要在 PR 里加测试文件
- 验证方式:`python -c "import app.main"` 验证 import 链
- 调试:`D:/miniConda/envs/rag/python.exe -c "..."` 跑单步验证

---

## 14. Troubleshooting 常见错误

| 症状 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: No module named 'app'` | Windows 下 `python app/main.py` 不把 cwd 加到 sys.path | 用 `python -m app.main` |
| `conda activate rag` 后 `python` 仍指向 base | 当前 shell 没真正激活 conda | 用绝对路径 `D:/miniConda/envs/rag/python.exe -m app.main` |
| `RuntimeError: 请设置环境变量 JWT_SECRET` | .env 没配或用了默认值 | 改 .env 的 `JWT_SECRET` / `PII_ENCRYPTION_KEY` |
| 启动卡在 `init_db` | PostgreSQL 没起 | `docker compose up -d` + `pg_isready` |
| 嵌入 429 限流 | SiliconFlow RPS 超限 | 调 `embedding_rate_limit_rps`(默认 5) |
| `file extension not supported` | 上传文件后缀不在白名单 | 允许:.pdf/.docx/.pptx/.xlsx/.html/.txt/.md/.csv/.png/.jpg/.jpeg/.gif/.bmp/.webp |
| 检索召回 0 结果 | KB 还没索引任何文档 | 先上传文档到 KB |
| `pgvector: could not open extension` | pgvector 扩展没装 | `docker compose restart postgres` 或重装 |
| `logs/` 目录没生成 | 没启动过任何请求(日志系统按需创建) | 跑一次 `python -m app.main` 即可 |
| `Unsupported file type: .xxx` | 扩展名不在 FILE_TYPE_MAP | 看 `app/ingestion/parser.py:22` 确认支持列表 |

---

## 15. 关键文件锚点(给 AI 助手快速跳转)

| 关注点 | 位置 |
|--------|------|
| RAG 主流程 | `app/core/pipeline.py:51` (`RAGPipeline.execute`) |
| 混合检索 SQL | `app/store/pgvector_store.py:88` (`search`) / `:152` (`bm25_search`) / `:199` (`hybrid_search` + RRF) |
| MMR 算法 | `app/core/mmr.py:12` (`mmr_select`) |
| PII 三层防御 | `app/core/pii_scanner.py:102` (`scan`) |
| 增量 hash 复用 | `app/ingestion/indexer.py:108` |
| 启动钩子 | `app/main.py:31` (`startup`) |
| 日志配置 | `app/core/logging.py:27` (`setup_logging`) |
| API 入口 | `app/main.py:24-28` (5 个 router include) |
| JWT 中间件 | `app/middleware/auth.py:56` (`get_current_user`) |
| 摄取主流程 | `app/ingestion/indexer.py:27` (`DocumentIndexer.index`) |
| 文档解析 | `app/ingestion/parser.py:35` (`DocumentParser.parse_bytes`) |
| 切块核心 | `app/ingestion/chunker.py:26` (`TextChunker.chunk`) |
| SSE 流式 | `app/api/chat.py:12` (`stream_chat`) |
| 前端 API 客户端 | `frontend/src/api/index.ts:3` (axios 实例) |
| 前端 SSE 解析 | `frontend/src/api/chat.ts:34` (`streamChat`) |

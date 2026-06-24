# RAGent — Agent 工作指南

## 初始化

```bash
# Python 后端（conda 环境 rag）
conda activate rag

# 数据库（仅 PostgreSQL + pgvector）
docker compose up -d

# 后端（reload 模式）
python app/main.py    # → http://localhost:8000

# 前端（Vue 3 + Vite + TS）
cd frontend
npm install
npm run dev           # → http://localhost:5173
```

默认管理员: `admin` / `admin123`

## Git LFS

所有 `.py` 源文件通过 Git LFS 存储。克隆后需要：

```bash
git lfs pull
```

否则本地文件是 LFS 指针而非 Python 源码。需要修改源码时务必先 `git lfs pull`。

## 项目结构

- `app/main.py` — FastAPI 入口（uvicorn reload 直起）
- `app/config.py` — pydantic-settings 配置，支持 `.env` 覆盖
- `app/api/` — 路由：auth, admin, chat(SSE), documents, kb
- `app/core/pipeline.py` — RAG 主流程（PII → 重写 → 意图 → 检索 → 重排 → 生成）
- `app/store/` — DB 模型 + 数据访问层（SQLAlchemy, pgvector, BM25）
- `app/ingestion/` — 文档处理管线（解析→清洗→切分→索引）
- `app/llm/` — LLM 客户端（MiniMax M3 chat, SiliconFlow embedding+rerank, vision）

## 关键技术细节

- **数据库**: PostgreSQL 15 + pgvector 0.8, 连接串 `postgresql://ragent:ragent@localhost:5432/ragent`
- **外部 API**: MiniMax M3（对话）, SiliconFlow（embedding + rerank），均需在 `.env` 配置 key
- **对话流**: SSE 事件流 (`event: token` / `event: done` / `event: error` / `event: metadata`)
- **检索管线**: 意图识别 → 查询改写 → 混合检索(BM25+向量) → RRF 合并 → 跨编码器重排 → MMR 多样性
- **启动钩子**: `init_db()` 迁移表和索引, `seed_defaults()` 种子角色/权限, `seed_pii_rules()` PII 规则
- **PII**: 正则 → 算法校验 → 上下文排除三层，支持 mask / reject / audit 策略
- **RBAC**: 8 项权限, 3 级知识库可见性 (public / internal / restricted)
- **无测试框架**：`test/` 目录仅有手工测试脚本，无 CI，无 lint/typecheck 检查
- **前端约束**: build 命令含 `vue-tsc -b && vite build`，但无运行时类型检查

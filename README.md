# RAGent — RAG 文档处理与智能问答系统

文档上传、解析、智能切分、向量检索、RBAC 权限控制、JWT 认证、流式对话一站式系统。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python + FastAPI |
| 前端 | Vue 3 + Vite + TypeScript |
| 数据库 | PostgreSQL 15 + pgvector 0.8 |
| 认证 | JWT + bcrypt（RBAC，8 项权限） |
| 对话 | MiniMax M3 |
| 向量 | Qwen3-VL-Embedding-8B（4096d）|
| 重排 | BAAI/bge-reranker-v2-m3 |

## 架构

```
[前端 :5173] → proxy → [FastAPI :8000]
                         ├── 认证 API（注册/登录/个人信息）
                         ├── 对话 API（SSE 流式）
                         ├── 文档 API（上传/列表）
                         ├── 知识库 API（CRUD）
                         └── 管理 API（用户/权限/PII 审核）

检索管线：
意图识别 → 查询改写 → 混合检索(BM25+向量) → RRF 合并
 → 跨编码器重排 → MMR 多样性(λ=0.7, 每文档≤2) → TopK
```

## 功能

- **文件解析**：PDF/DOCX/PPTX/XLSX/HTML/TXT/MD/图片（Docling + MiniMax Vision）
- **智能切分**：结构感知递归切分，原子块保护（代码/表格/图片），重叠窗口
- **混合检索**：向量语义检索 + BM25 关键词检索（jieba 分词）+ RRF 融合
- **MMR 多样性**：最大边际相关性重排，跨文档语义去重，每文档软约束
- **长对话记忆**：近期 N 轮 + 压缩摘要，代词指代消解查询改写
- **PII 安全红线**：身份证/手机/邮箱/银行卡三层检测（正则 → 算法校验 → 上下文排除），敏感内容脱敏或拒审
- **文档增量更新**：内容 hash 比对，仅处理新增/修改 chunk，复用已有 embedding
- **RBAC 权限**：8 项权限（chat / doc.upload / doc.read_all / kb.create / kb.delete / kb.manage_visibility / user.manage / admin）
- **知识库隔离**：public / internal / restricted 三级可见性
- **管理员 PII 审核**：告警确认/误报/白名单，加密暂存敏感文档
- **前端风格**：Apple 设计语言，系统字体栈，零图标库，SVG 图标

## 快速开始

### 1. 数据库（PostgreSQL + pgvector）

```bash
docker compose up -d
```

### 2. 后端

```bash
conda activate rag   # 或 pip install -r requirements.txt
unset SSL_CERT_FILE  # conda 环境可能需要
python app/main.py
# → http://localhost:8000
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### 4. 登录

默认管理员：`admin` / `admin123`

## 项目结构

```
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # pydantic-settings 配置
│   ├── api/                    # 路由处理器
│   │   ├── chat.py             # SSE 流式对话
│   │   ├── documents.py        # 文档上传/列表（支持增量更新）
│   │   ├── admin.py            # 用户管理 + PII 审核
│   │   ├── auth.py             # 注册/登录
│   │   └── kb.py               # 知识库 CRUD
│   ├── core/                   # 核心管线
│   │   ├── pipeline.py         # RAG 主流程（意图 → 检索 → 生成）
│   │   ├── retrieval.py        # 混合检索 + MMR 多样性
│   │   ├── mmr.py              # MMR 算法（归一化 + 文档惩罚）
│   │   ├── memory.py           # 长对话记忆管理
│   │   ├── rewrite.py          # 查询改写 + 代词消解
│   │   ├── intent.py           # 意图分类
│   │   ├── prompt.py           # Prompt 组装
│   │   └── pii_*.py            # PII 检测引擎
│   ├── ingestion/              # 文档处理管线
│   │   ├── parser.py           # 多种格式解析
│   │   ├── cleaner.py          # 文本清洗
│   │   ├── chunker.py          # 结构感知切分
│   │   ├── metadata.py         # LLM 元数据生成
│   │   ├── indexer.py          # 索引（含增量更新 + PII 过滤）
│   │   └── pipeline.py         # 编排
│   ├── llm/                    # LLM 客户端
│   │   ├── chat.py             # MiniMax M3 对话
│   │   ├── embedding.py        # SiliconFlow embedding
│   │   ├── rerank.py           # 跨编码器重排
│   │   └── vision.py           # 图片理解
│   ├── middleware/auth.py      # JWT 认证中间件
│   ├── models/schemas.py       # Pydantic 数据模型
│   └── store/                  # 数据访问层
│       ├── db.py               # SQLAlchemy 模型 + 连接
│       ├── auth_store.py       # 用户/角色/权限 CRUD
│       └── pgvector_store.py   # 向量检索 + BM25 + 混合搜索
├── frontend/
│   └── src/                    # Vue 3 SPA
├── docker/
│   └── Dockerfile              # postgres:15 + pgvector
├── docker-compose.yml
└── requirements.txt
```

## 关键配置

参见 `app/config.py`，包含：

- **PII**：开关、缓存 TTL、加密密钥
- **混合检索**：开关、单路候选数、RRF 常数
- **MMR**：开关、λ 值、候选数、每文档上限、惩罚系数
- **对话**：轮数上限、摘要触发轮数、最大 token 数

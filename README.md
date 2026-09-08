# RAGent — RAG 文档处理与智能问答系统

企业级知识库 RAG 系统：多格式文档摄入 → 表格感知切分与归一化 → 混合检索（向量 + BM25 + 候选问题三通道）→ 跨编码器重排 → MMR 去重 → 带引用的流式生成。配套 RBAC/JWT 权限、PII 三层检测、全链路诊断、确定性评测系统（Evaluator v1）与数据字典 MCP 服务。

> 项目截图见 [docs/screenshots/](docs/screenshots/)。

## 功能特性

- **多格式解析**：PDF / DOCX / PPTX / XLSX / HTML / TXT / MD / 图片（Docling + Qwen3-VL-8B-Instruct 视觉理解）
- **结构感知切分**：按标题层级（H3/H2）语义切分，原子块保护（表格/代码/图片整体不拆），超限元素边界装箱 + 文本级硬切（带 64 字符重叠窗口）
- **表格感知摄入（Issue #2 第一期）**：表格双表示——`chunks.text` 保留原始 Markdown（生成用），`retrieval_text` 逐行归一化为自包含语句（检索/embedding 用，解决"列-值对应关系丢失"）；`chunk_type / table_headers / table_meta` 元数据列落库
- **混合检索**：向量语义 + BM25（jieba 分词、OR-tsquery）+ 候选问题通道，RRF 融合；两阶段检索（文档级筛选 → 块级）；relaxed-BM25 兜底
- **多路召回（question channel）**：摄入期 LLM 为每个 chunk 生成候选问题 → 独立 embedding → 查询期三路 RRF（权重 0.15 防噪声）；默认开启，可 env 关闭
- **跨编码器重排 + MMR 多样性**：bge-reranker-v2-m3 精排；MMR（λ=0.7，每文档 ≤2）控制冗余
- **RAG v2 可选策略**（代码齐备、默认关闭，见下方检索链路表）：跨文档关联检索 / 权威 section 加权 / section 补充 / 跨年覆盖补充 / 查询拆解 / Evidence Gate（证据覆盖不足时拒答）
- **长对话记忆**：Token 预算窗口 + 超限自动摘要压缩 + 有界滞后；思考/回答双流推送，支持中断恢复
- **PII 安全红线**：身份证/手机/邮箱/银行卡三层检测（正则 → Luhn/mod-11 算法校验 → 上下文排除），脱敏或拒审，管理员审核（确认/误报/白名单）
- **文档增量更新**：内容 hash 复用 —— 未变更 chunk 复用 embedding 与 LLM 元数据；embedding 部分失败时保留旧索引可重试
- **RBAC + 三级知识库**：8 项权限（chat / doc.upload / doc.read_all / kb.create / kb.delete / kb.manage_visibility / user.manage / admin）；public / internal / restricted 可见性
- **故障隔离**：熔断器按 provider 隔离、5xx/429 自动降级（BM25-only / 跳过重排 / 备用响应）、DB 故障与"真无内容"分流
- **全链路诊断**：每次查询的检索链路 JSON 化（rewrite → intent → retrieve → rerank → mmr → stream），浏览器可视化
- **数据字典 MCP 服务**：独立 MCP stdio 服务，PG 自省 → 确定性 Markdown 渲染 → 摄入本系统 KB，向外部 LLM（如 ReportAgent）提供表结构/FAQ 检索工具
- **索引完整性检查**：`tools/index_integrity_report.py` —— re-index 后硬断言（文档数 / chunk 数 / question 覆盖率），历史上两次"配置与数据错配"事故由此显式 FAIL
- **确定性评测系统（Evaluator v1）**：gold 唯一入口 + 校验器；judge 评分 rubrics；确定性数字校验辅助；15 题 verified regression gate + 65 题正式 benchmark（详见「评测系统」）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 + FastAPI（异步） |
| 前端 | Vue 3 + Vite + TypeScript + Pinia |
| 数据库 | PostgreSQL 15 + pgvector 0.8（向量 + GIN tsvector + 关系矩阵） |
| 认证 | JWT + bcrypt，RBAC 8 权限 |
| 对话 / 意图 / 视觉 | SiliconFlow：Qwen3-8B（对话、意图路由）；DeepSeek-R1-0528-Qwen3-8B（复杂查询拆解）；Qwen3-VL-8B-Instruct（图片） |
| 向量 | Qwen/Qwen3-Embedding-8B（**1024 维**，代际 v3） |
| 重排 | BAAI/bge-reranker-v2-m3 |
| 数据字典 | mcp_server/（`mcp>=2.0`，stdio） |

## 架构

```
                          ┌────────────────────────── MCP 数据字典服务（独立进程） ─────────────────────────┐
                          │  mcp_server/  PG 自省 → render.py 确定性 Markdown → HTTP 上传 KB → 检索工具      │
                          └──────────────────────────────────┬────────────────────────────────────────────┘
[前端 :5173] ── proxy ──► [FastAPI :8000]                    │
                            ├── /api/v1/auth                 │  上传（增量 hash 复用 + PII 过滤）
                            ├── /api/v1/chat/stream  (SSE)   ▼
                            ├── /api/v1/documents      ┌────────────────────────────────────────────┐
                            ├── /api/v1/kb             │ 摄入：Parser → Cleaner → Structurer →       │
                            ├── /api/v1/retrieve       │ Chunker（原子块保护）→ Metadata（LLM 批量） │
                            ├── /api/v1/admin          │ → Indexer（双表示 + 代际标记 + 关系矩阵）    │
                            └── /api/v1/diag           └──────────────────────┬─────────────────────┘
                                                                              │
   查询：QueryRewrite → IntentClassify（路由 1-3 个 KB）→ Hybrid Search（三路 RRF / 两阶段）
         → Rerank → MMR → TopK → Prompt 组装（含证据冲突提示）→ SSE 流式（思考/回答分离）
```

## 检索链路

```
query
  ↓ QueryRewrite（代词消解 / 复杂查询拆解，DeepSeek-R1）
  ↓ IntentClassify（Qwen3-8B，路由到 1-3 个知识库）
  ↓ Hybrid Search
      ├─ 向量通道   Qwen3-Embedding-8B@1024（表格 chunk 用归一化 retrieval_text）
      ├─ BM25 通道  jieba 分词 + PG ts_rank（OR-tsquery；< top_k 时 relaxed 兜底）
      └─ 问题通道   摄入期生成的候选问题向量（RRF 权重 0.15）
      └─ RRF 融合（可加文档级两阶段预筛）
  ↓ Cross-encoder Rerank（bge-reranker-v2-m3）
  ↓ MMR 多样性（λ=0.7，每文档 ≤2，跨文档软约束）
  ↓ TopK → Prompt 组装（证据表 + 冲突提示）→ SSE 流式生成（TagStreamParser 统一 think/answer 流）
```

**RAG v2 可选策略**（8 组 ablation 对三一语料无 recall 收益，默认关闭；需要时用 env 单点重评估）：

| 策略 | env | 说明 |
|---|---|---|
| 跨文档关联检索 | `CROSS_DOC_ENABLED` | 三通道跳转：TF-IDF 关系边（摄入期预建）/ query 关键词 / 文档级 embedding；查询期零 LLM 成本 |
| 权威 section 加权 | `SECTION_BOOST_ENABLED` | 财务关键词触发，权威 section（主要会计数据/利润表）boost |
| section 补充 | `SECTION_SUPPLEMENT_ENABLED` | 权威 section 定向补充检索 |
| 跨年覆盖补充 | `YEAR_SUPPLEMENT_ENABLED` | C 类跨年 query 缺失年份定向补 1 条 |
| 查询拆解 | `QUERY_DECOMPOSITION_ENABLED` | 复杂 query 拆子问题 |
| Evidence Gate | `EVIDENCE_GATE_ENABLED` | 证据覆盖率 < 阈值或高严重度冲突时拒答（代码齐备，默认关） |

始终开启：问题通道（`QUESTION_CHANNEL_ENABLED`，env 可关，Baseline 评测在 OFF 下锁定）、检索缓存、降级提示。

## 快速开始

### 1. 数据库（PostgreSQL 15 + pgvector）

```bash
docker compose up -d
```

### 2. 后端（Python 3.11）

```bash
# Windows 下建议直接用绝对路径（避免 conda activate 指向 base 环境）
D:/miniConda/envs/rag/python.exe -m app.main
# → http://localhost:8000
```

> 必须用 `python -m app.main`（`python app/main.py` 不把项目根加入 sys.path）。`.env` 需配置 `SILICONFLOW_API_KEY`、`JWT_SECRET`、`PII_ENCRYPTION_KEY`（见 [.env.example](.env.example)）。

### 3. 前端

```bash
cd frontend
npm install        # 首次或依赖变更后
npm run dev
# → http://localhost:5173
```

### 4. 登录

默认管理员：`admin` / `admin123`

## 项目结构

```
├── app/
│   ├── main.py                 # FastAPI 入口（idempotent init_db）
│   ├── config.py               # pydantic-settings：全部配置（含策略开关）
│   ├── api/                    # chat(SSE) / documents / kb / auth / admin / diagnostics / retrieve
│   ├── core/                   # RAG 管线
│   │   ├── pipeline.py         # 主流程编排 + evidence gate 接入点
│   │   ├── retrieval.py        # 混合检索 + RRF + 策略（section/year/cross-doc）
│   │   ├── doc_relation.py     # 跨文档三通道跳转（摄入期预建关系矩阵）
│   │   ├── evidence.py         # 证据整理层：冲突检测（ConflictKey 5 元组）+ 格式化
│   │   ├── mmr.py / memory.py / rewrite.py / intent.py / prompt.py
│   │   ├── tag_parser.py       # think/answer 标签流解析（纯逻辑，12+ 单测）
│   │   ├── pii_scanner.py / pii_rules.py
│   │   └── diagnostics.py      # 每次查询链路遥测（JSON + index）
│   ├── ingestion/
│   │   ├── parser.py           # 多格式 → Markdown（Docling + 视觉）
│   │   ├── cleaner.py / structurer.py（表格识别，is_atomic）
│   │   ├── chunker.py          # 语义切分 + 原子块保护 + 超限装箱/硬切
│   │   ├── retrieval_text.py   # 表格归一化（表格感知双语表示）
│   │   ├── metadata.py         # LLM 元数据：25 chunks/批 + 每批重试 + 降级不丢块
│   │   └── indexer.py          # hash 复用 + 代际标记 + 问题向量 + 完整性
│   ├── llm/                    # AsyncOpenAI 封装：chat / embedding / rerank / vision
│   ├── middleware/auth.py      # JWT + RBAC
│   └── store/                  # SQLAlchemy 模型 + pgvector_store（hybrid_search）
├── frontend/src/               # Vue 3 SPA（Apple 设计语言，零图标库）
├── mcp_server/                 # 数据字典 MCP stdio 服务（PG 自省 → 渲染 → 上传 KB）
├── eval/                       # 评测系统（Evaluator v1）
│   ├── gold.py                 # ★ rag_testset.json 唯一读取入口 + schema 校验 + self-check
│   ├── baseline_eval.py        # 15 题 verified regression gate（--name/--compare）
│   ├── eval_sany.py            # 65 题正式 benchmark（judge 评分）
│   ├── eval_detail.py / eval_single.py / rejudge.py
│   ├── judge_rubric.py         # 3 分制评分标准（含"错误拒答判 0 分"）
│   ├── deterministic_numeric.py# 数字断言辅助（B 类）
│   ├── metrics.py              # 检索指标（hit@k / recall@k / mrr）
│   └── sany_annual_reports/    # 三一重工 2023-2025 年报评测集 + baseline 产物
├── tools/
│   ├── index_integrity_report.py  # ★ 索引完整性硬断言（re-index 后必跑）
│   ├── diagnostics.html           # 检索链路可视化（浏览器直开）
│   ├── migrate_vector_dimension.py / reembed_v2.py / reset_rag_corpus.py  # 一次性迁移工具
│   └── run_ablation.py / smoke_models.py
├── tests/                      # pytest：tests/unit（离线）+ tests/integration（ragent_test 库）
├── docs/
│   ├── plans/                  # 实施计划索引（含全部 baseline 锁定文档）
│   ├── architecture-decisions.md  # 主要技术决策与 trade-off（ADR）
│   └── screenshots/            # 项目截图
├── docker/ + docker-compose.yml
└── requirements.txt / requirements-dev.txt（pytest + ruff）
```

## 评测系统（Evaluator v1）

针对三一重工 2023-2025 年报的评测集（65 题，10 个类别 A-J：单文档事实 / 表格理解 / 跨文档对比 / 计算推理 / 时序追溯 / 口径辨析 / 实体消歧 / 错误前提 / 拒答边界 / 细节脚注）。

**数据流**：`eval/sany_annual_reports/rag_testset.json` → [`eval/gold.py`](eval/gold.py)（唯一入口，含 schema 校验）→ 各评测入口。新增脚本一律走 `gold.py`，禁止直接 `open` 测试集文件。

```bash
# gold 自检（唯一入口校验）
D:/miniConda/envs/rag/python.exe eval/gold.py

# 15 题 verified regression gate（唯一变量对比用 --compare）
D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-x --compare baseline-3

# 65 题正式 benchmark（judge 3 分制；约 30-60 分钟，含限速间隔）
D:/miniConda/envs/rag/python.exe eval/eval_sany.py

# 按类别 / 单题 / 重评
D:/miniConda/envs/rag/python.exe eval/eval_detail.py --category A --limit 10
D:/miniConda/envs/rag/python.exe eval/eval_single.py
D:/miniConda/envs/rag/python.exe eval/rejudge.py
```

**Baseline 阵容（labeling：Baseline-N = 正式锁定档位；后缀 R = 重建/重放）**：

| Baseline | 配置 | 结果 | 状态 |
|---|---|---|---|
| Baseline-1R | DeepSeek-V3 + Qwen3-VL-Embedding@4096，v1 语料 | acc(≥2) 50.0%（14/15 覆盖） | 历史参照 |
| Baseline-2 | Qwen3-Embedding-8B@**1024**，重新摄入 1381 chunks @v2，channel OFF | 66.7%（15 题）| LOCKED |
| Baseline-3 | 表格感知摄入（双表示 + 元数据列），1340 chunks @v3，channel OFF | **73.8%**（65 题，mean 2.23/3，覆盖 65/65）| LOCKED |
| Baseline-3R | 重建控制组：1381 chunks，metadata batching 修复，channel OFF | 66.7%（15 题）| 控制组（非新功能） |
| Baseline-4 候选 | 1381 chunks，channel ON | INVALID（metadata 单次调用缺陷丢 41 chunks，coverage 0.94%）| 已归档 |

方法论要点：15 题小样本被 ±50pp 单题翻转证明无统计力，正式锁定以 65 题 benchmark 为准（见 `docs/plans/2026-08-24-baseline-3-locked.md`）；评测时 judge ≠ 生成模型（同一 API 密钥下用独立 model 参数）。

## 测试

```bash
# 安装测试依赖（首次）
D:/miniConda/envs/rag/python.exe -m pip install -r requirements-dev.txt

# 全量：unit（离线）+ integration（ragent_test 库，PG 不可达自动 skip）
D:/miniConda/envs/rag/python.exe -m pytest -q        # 当前基线：589 passed / 13 skipped / 0 failed

# 全量真 API 冒烟（需要 .env 真实 key，约 10 分钟）
RAGENT_LIVE_LLM=1 D:/miniConda/envs/rag/python.exe -m pytest tests/integration -m live_llm -v
```

- `tests/unit` 完全离线：`conftest.py` 在任何 app 模块导入前把凭据换成哨兵值，误触真实 DB/网络/LLM 立即失败
- `tests/integration` 只用自动创建的独立 `ragent_test` 库，确定性 fake embedding/rerank/metadata 层（md5 词袋 1024 维），不写开发库、不调真实 LLM
- 质量门禁：`ruff check .` + `ruff format --check .`（`docs/plans` 的 markdown 由 ruff.toml 排除——历史实验记录只保原始事实）

## 工具

```bash
# 索引完整性硬断言（配置与数据错配显式 FAIL）
D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py --expect-documents 3 --expect-questions zero

# 检索链路可视化（浏览器直接打开）
# → tools/diagnostics.html（读取 /api/v1/diag 的遥测 JSON）

# 数据字典 MCP 服务（供外部 LLM 消费表结构/FAQ）
D:/miniConda/envs/rag/python.exe -m mcp_server.server
# 环境变量：RAGENT_URL / RAGENT_USER / RAGENT_PASSWORD / DICT_PG_DSN / DICT_KB_NAME / FAQ_KB_NAME
```

## 关键配置

见 [`app/config.py`](app/config.py)，要点：

- **LLM**：SiliconFlow（provider 熔断隔离，MiniMax 可作 fallback）
- **Embedding**：`embedding_model` / `embedding_dimension`（1024）/ `current_embedding_version`（代际 v3）——切换模型后需 `tools/migrate_vector_dimension.py` + 重摄
- **策略开关**：见上方「RAG v2 可选策略」表
- **PII**：三层检测开关、脱敏策略（mask full/partial / reject / audit）
- **MMR**：λ=0.7、每文档上限、惩罚系数（均可调）
- **对话**：轮数上限、摘要触发轮数、token 预算

## 已知限制（Known Limitations）

1. **C 类（跨文档/跨年度对比）召回仍偏弱**：65 题下 C 类 50%。跨年覆盖率补充/跨文档关联三通道策略代码齐备，但 8 组 ablation 在当前语料无 recall 收益，默认关闭。
2. **B 类（单位换算）存在数字滑移**：千元/亿元换算偶发误差；judge 将其判为明显错误。
3. **Question channel 保留为实验项**：Baseline-3R 控制组（channel OFF）已建立；channel ON 的 Baseline-4 因 metadata 单次调用缺陷标记 INVALID（该缺陷已修复：25 chunks/批 + 重试 + 显式键映射），重跑 Baseline-4 作为后续实验，未纳入本次封板。
4. **15 题回归门无统计力**：2 题类别被 ±50pp 单题翻转击穿两次；正式结论一律以 65 题 benchmark 为准。
5. **引用对齐无自动化验证测试**：`[1][2]` 引用规则在 system prompt 中强制，人工标注里有 citation 维度，但缺少"答案引用 ↔ 检索来源"的自动化对齐测试。
6. **Evidence Gate 默认关闭**：代码级拒答保护（覆盖率阈值 + 高严重度冲突）已实现并有 4 组测试，但因需要独立校准实验，默认不激活。
7. **表格检索二期未实现**：`chunk_type/table_headers/table_meta` 已落库（一期），基于 chunk_type 的 table-only 检索/重排（二期）未做。

## 更多文档

- [AGENTS.md](AGENTS.md) — 开发/协作主指南
- [docs/plans/README.md](docs/plans/README.md) — 实施计划总索引（含全部 baseline 锁定与 ablation 报告）
- [docs/architecture-decisions.md](docs/architecture-decisions.md) — 主要技术决策与 trade-off
- [CLAUDE.md](CLAUDE.md) — Claude Code 协作配置（与 AGENTS.md 互为补充）

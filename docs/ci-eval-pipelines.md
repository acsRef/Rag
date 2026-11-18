# CI / 检索评测流水线

本仓库有两条 GitHub Actions 流水线，分工不同：

| 流水线 | 文件 | 回答的问题 | 触发 | 需要 secret |
| --- | --- | --- | --- | --- |
| L1 门禁 | `.github/workflows/ci.yml` | 「代码有没有被改坏」 | 任意分支 push / 所有 PR | 否 |
| L2 真检索评测 | `.github/workflows/eval.yml` | 「检索层现在还能不能把 gold 文档捞回来」 | 每天 02:17（Asia/Shanghai）+ 手动 | `SILICONFLOW_API_KEY` |

真 embedding 检索评测**不在** L1 门禁里：它消耗真实 API 额度、每次要重建 3 份年报语料，属于「每晚一次」的 L2，不应拦住日常提交。

---

## L1：`ci.yml`

两个并行 job，都只装 `requirements.txt` + `requirements-dev.txt`，不需要任何 secret：

| job | 命令 | 说明 |
| --- | --- | --- |
| `unit` | `python -m pytest tests/unit -q` | 纯离线：`tests/conftest.py` 在 import `app` 前把 DB / LLM 凭据换成哨兵值，误触真实服务的用例会立即失败 |
| `integration` | `python -m pytest tests/integration -q` | 真 PostgreSQL 15 + pgvector service；embedding / rerank / metadata 三层由 `tests/integration/conftest.py` 的确定性 fake 实现替换，所以同样不需要 API key |

**没有 key 时会不会红？不会。** 真实 API 用例（`test_live_llm.py` / `test_live_long_conversation.py` / `test_live_pdf_ingest.py` / `test_live_table_docs.py`）靠 `pytestmark = pytest.mark.live_llm` + `live_env` fixture 双重保护：未设置 `RAGENT_LIVE_LLM=1`（或 `.env` 无真实 key）时结果一律是 `skipped`。

本地复跑（Windows，rag 环境）：

```bat
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q
:: integration 需要本机 PostgreSQL（docker compose up -d），否则整批 skip
D:/miniConda/envs/rag/python.exe -m pytest tests/integration -q
```

---

## L2：`eval.yml`

### 触发

- `schedule: cron "17 18 * * *"` → 每天 18:17 UTC = 次日 **02:17 Asia/Shanghai**；
- `workflow_dispatch` 手动触发，输入 `tier`（`full` / `regression` / `smoke`，默认 `full` = 全部 65 题）和 `top_k`（默认 `10`）。

### job 1：`retrieval-invariants`（不需要 secret）

直接复用 `tests/integration` 里**已经存在**的场景测试，不新写场景逻辑：

| 测试文件 | 覆盖的不变式 |
| --- | --- |
| `test_ingestion.py` | 增量摄入 / `content_hash` 复用（无重复嵌入、无重复 chunk） |
| `test_delete_relations.py` | 删除文档 / KB 后 `doc_relations` 无幽灵边（删除后无脏召回） |
| `test_workspace_acl.py` | `restricted` 工作空间 + owner 旁路（跨库隔离 / 权限） |
| `test_cross_doc.py` | 跨文档关系矩阵（跨库召回边界） |
| `test_retrieval_e2e.py` | 检索全链路 embedding → hybrid → cross-doc → rerank → MMR |

### job 2：`retrieval-eval`（需要 `secrets.SILICONFLOW_API_KEY`）

复用仓库既有评测入口，**没有另造判据**：

| 环节 | 复用的既有实现 |
| --- | --- |
| testset 唯一入口 / 校验 | `eval/gold.py`（`load_testset` / `iter_questions` / `validate`，CLI 直接 `python eval/gold.py` 做 schema 校验） |
| 选题（tier） | `eval/retrieval_eval.py::_select_questions` + `eval/sany_annual_reports/tiers.json` |
| 检索调用与逐题指标 | `eval/retrieval_eval.py::main` → `POST /api/v1/retrieve` |
| 指标口径 | `eval/metrics.py::compute_all`（`hit@5` / `hit@10` / `recall@5` / `recall@10` / `mrr`） |
| 语料与 gold 文档 id | `tools/annotate_gold_documents.py::YEAR_TO_DOC_ID`（2023/2024/2025 → gold document_id） |

执行顺序：

1. `CREATE EXTENSION vector`（`app/store/db.py` 的 `init_db()` 不建 extension，但建表需要它）；
2. `python eval/gold.py` —— gold 测试集 schema 自检，坏了就提前失败，不浪费后面 20 分钟；
3. 起后端 `python -m app.main`（`JWT_SECRET` / `PII_ENCRYPTION_KEY` 用 CI 占位值，只用于过启动强校验），轮询 `/health`；
4. 重建语料：把 `eval/sany_annual_reports/*.pdf`（仓库内的 3 份年报，已 track）转成 Markdown，按 `/api/v1/documents/upload` 的既有契约入库；
5. `python eval/retrieval_eval.py --tier "$TIER" --top-k "$TOP_K"`；
6. 聚合指标写 Job Summary，并上传 artifact。

**为什么第 4 步要绕一圈、而不是直接上传 PDF？** 两个原因，都是当前仓库的真实约束：

- `rag_testset.json` 的 `gold_documents` 是历史一次入库产生的 document_id（`c70ad0dd…` / `f4b0be8a…` / `aebd3be2…`）。新库 `new_id()` 随机生成 id，直接上传会让**每一题的 hit@k 恒为 0**（本地已复现：用开发库现有语料跑 `eval/retrieval_eval.py --tier smoke`，指标全 0）。所以流程先按上传契约预建 `Document` 行（`app/api/documents.py::_upsert_processing_document`），再用 `document_id` 表单字段重新上传，把 gold id 钉死。
- `app/ingestion/parser.py::_handle_pdf` 走 `pymupdf4llm`，默认启用 GNN layout 模式，在 CI 的 CPU 上对 250 页年报极慢（本地 3 份并发 >15 分钟仍未完成）。流程里显式 `pymupdf4llm.use_layout(False)` 走经典路径（仍保留 Markdown 表格），本地实测 3 份合计约 2 分钟。
- 语料入库设置 `INGEST_METADATA_ENABLED=0`：元数据 LLM 会按 25-chunk 批次逐批调用（`app/ingestion/metadata.py` 注释：重灌 3 份年报要 1–2 小时），关掉后入库只做「分块 + embedding」，分钟级完成。代价是 question 通道（`chunk_questions`）为空，指标会略低于生产语料——**看趋势，不做绝对分数对齐**。

### secret 与「没配时」的行为

唯一需要的 secret：

| secret | 用途 | 没配时 |
| --- | --- | --- |
| `SILICONFLOW_API_KEY` | 真实 embedding（`/api/v1/retrieve` 的查询向量化） | 第一个 `guard` step 打印 `::warning::` + Job Summary「已跳过」，后续步骤全部 `if` 跳过，**job 保持绿色**，不会红 |

配置方式：仓库 *Settings → Secrets and variables → Actions → New repository secret*。用于「不需要 key 也能跑」的部分（`retrieval-invariants`、L1）不受影响。

### 产物与失败排查

- **artifact**：`retrieval-results-<run_id>` —— `eval/sany_annual_reports/retrieval_results.json`（逐题 `retrieved_ids` / `gold_documents` / `metrics` / 耗时）+ `ragent-backend.log`（后端 & ingest 日志），保留 30 天。
- **Job Summary**：指标表 + 题目数 / 报错数 / 平均检索耗时。
- **自检门（不是质量门）**：如果 `retrieval_results.json` 里没有任何 gold 标注、或所有指标为 0，step 以 `::error::` 失败——这种「绿着但没信号」的夜间评测比红更危险（通常意味着语料 document_id 与 gold 未对齐）。
- 失败时按顺序看：`Install dependencies`（依赖装不上）→ `Start backend` 日志尾（后端没起来会打印 100 行日志）→ `Build eval corpus`（ingest 状态 `failed` 会带 `error_message`）→ artifact 里的 backend 日志。
- 指标不达预期时：先用 `--tier smoke`（10 题）复跑确认链路，再看 `retrieval_results.json` 里逐题 `top1_doc` 是否落在正确年份的年报上。

### 本地复跑（完整 L2 链路）

前提：本机 PostgreSQL + pgvector（`docker compose up -d`）、`.env` 里有真实 `SILICONFLOW_API_KEY`、`JWT_SECRET`、`PII_ENCRYPTION_KEY`。

```bat
:: 0) 依赖（pymupdf4llm 未声明在 requirements.txt，见下方「已知限制」）
D:/miniConda/envs/rag/python.exe -m pip install -r requirements.txt -r requirements-dev.txt
D:/miniConda/envs/rag/python.exe -m pip install pymupdf4llm==1.28.2

:: 1) gold 自检
D:/miniConda/envs/rag/python.exe eval/gold.py

:: 2) 起后端（另开终端，保持运行）
D:/miniConda/envs/rag/python.exe -m app.main

:: 3) 按 workflow 里的「Build eval corpus」同样方式准备语料后跑评测
D:/miniConda/envs/rag/python.exe eval/retrieval_eval.py --tier smoke --top-k 10
D:/miniConda/envs/rag/python.exe eval/retrieval_eval.py --tier full --top-k 10
```

> 注意：若本地开发库的语料不是用 gold document_id 入库的（比如最近重灌过），`retrieval_eval.py` 会照跑不误但指标全 0——这正是 L2 要钉死语料 id 的原因。

---

## 已知限制（不在本次范围内修）

1. `pymupdf4llm`（以及 `pymupdf`）被 `app/ingestion/parser.py` 的 PDF 解析路径 import，但没有写进 `requirements.txt`；干净环境上传 PDF 会失败（`_handle_pdf` 的 fallback `_parse_pdf_basic` 也依赖 `fitz`/`pymupdf`）。L2 在 workflow 里显式补装了 `pymupdf4llm==1.28.2`。
2. 每次 L2 都在空库上重建语料（PDF → Markdown → 分块 → embedding），因此 nightly 指标与 `docs/plans/2026-08-24-baseline-3-locked.md` 里 locked 的 65-Q 生成评测分数**不可直接比较**：语料路径（经典 pymupdf4llm 路径、metadata 关闭）不同，评测口径也不同（这里是 retrieval-only，不含 LLM 生成 / judge）。
3. L2 没有质量阈值门（不因指标下滑而红）——仓库既有的 65 题评测（`eval/eval_sany.py` 等）也没有阈值，本次不新造判据。后续若需要阈值，建议基于 artifact 里的历史 JSON 定基线后再加。
4. 真 LLM 生成评测（`eval/eval_sany.py` / `eval/eval_single.py`，65 题 × 真 chat + judge，约 10 分钟以上且更贵）仍留在手动路径，不在本流水线里。

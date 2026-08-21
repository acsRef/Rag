# Phase 1 清理轮执行计划（Cleanup Pass Implementation Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec [docs/plans/2026-08-21-cleanup-phase1.md](2026-08-21-cleanup-phase1.md) 完成文档一致性、死代码清理、Ruff 引入、类型风格统一、注释去日志化，全程不改运行时语义（唯一例外：两个静态检查发现的既有缺陷修复）。

**Architecture:** 6 个独立 commit（A 文档 → B 死代码 → C1 Ruff 配置+修复 → D 类型 → E 注释 → C2 format），每个 commit 后跑 unit 快检，收尾全量测试与 Phase 0 基线逐条对齐。

**Tech Stack:** Python 3.11 / conda env `rag`（一律用 `D:/miniConda/envs/rag/python.exe`）/ ruff（本轮引入）/ pytest。

---

## 基线（Phase 0，不可变）

全量 pytest：`457 passed, 6 failed, 13 skipped`。预存失败（结束时必须不多不少同为这 6 条）：

```
tests/integration/test_cross_doc.py::test_cross_doc_extras_reach_final_results
tests/integration/test_ingestion.py::test_reindex_partial_embed_failure_preserves_old_index
tests/integration/test_ingestion.py::test_failed_doc_can_be_retried
tests/integration/test_ingestion.py::test_questions_align_with_persisted_chunks
tests/unit/test_chunker.py::test_oversized_section_packs_on_element_boundaries
tests/unit/test_retrieval_year_coverage.py::test_supplement_appends_missing_related_years
```

评测基线不重跑：hit@10/recall@10 = 1.000，MRR ≈ 0.85–0.87，generation 73.3%（见 ablation-report）。

## 侦察结论（已核实的完整违规清单）

### F401 unused import（34 处，处置逐条列出）

| 位置 | 处置 |
|---|---|
| app/api/auth.py:5 `fastapi.status` | 删 |
| app/api/auth.py:10 `seed_defaults` | 从 import 列表删该名字（`seed_defaults` 的真实消费方是 main.py:16） |
| app/core/pipeline.py:5 `RetrievalFilter` | **真 bug 配套**：retrieval.py 用了字符串注解却没导入。pipeline.py 的这份删；retrieval.py 补真实导入（见 Task 5） |
| app/ingestion/metadata.py:12 `re`、:17 `settings` | 删 |
| eval/test_serial.py:3 `os`、:4 `sys` | 删 |
| tests/integration/test_live_llm.py:2 `os` | 删 |
| tests/integration/test_pipeline_db_degradation.py:9 `asyncio`、:11 `pytest`、:83 `app.llm.base` | 删（pytest.ini `asyncio_mode=auto`，async 测试无需显式导入） |
| tests/integration/test_summary_concurrency.py:37 整行三名字 | 整行删 |
| tests/integration/test_cross_doc.py:2 `pytest`、test_ingestion.py:4 `pytest` | 删（不影响其预存失败状态） |
| tests/unit/test_breaker_threads.py:9 `time`、:11 `app.llm.base`、:13 `PermanentError` | 删 |
| tests/unit/{test_evidence,test_faq_render,test_mmr,test_pipeline_helpers,test_prompt,test_rerank_client,test_retrieval_parallel,test_search_breaker,test_strategy_guards}.py 的 `pytest` | 各删一行 |
| tests/unit/test_query_parser.py:10 `pytest`、:12 `ParsedQuery` | 删 |
| tests/unit/test_rewrite_guard.py:5 `pytest`、:8 `app.core.rewrite` | 删 |
| tools/annotate_gold_documents.py:21 `sys`、tools/test_embedding_enhancement.py:2 `asyncio` | 删 |

### E402 import 不在文件头（15 处）

| 位置 | 处置 |
|---|---|
| app/core/doc_relation.py:42（stopwords 导入带"设计审查 P2-13"注释悬在代码中段） | 移到文件头 import 区，注释改写为 why 风格并入 |
| app/store/db.py:112-113（contextmanager/Generator 在模型定义后） | 移到文件头 |
| app/store/pgvector_store.py:26 | 查看内容移到文件头 |
| app/store/pgvector_store.py:976（`import numpy as np` 悬在函数前） | 移到文件头（numpy 第三方无环风险） |
| tests/unit/test_dict_server.py:9-10、test_faq_server.py:9-10 | 先看是否 sys.path hack：是 → 加 `# noqa: E402`（附一句理由）；否 → 上移 |
| tests/unit/test_retrieve_api.py:159-165 | 同上判断（疑似 app 对象先建后导路由的结构性模式） |

### F811 重定义（2 处）

- app/llm/embedding.py:13 —— base.RateLimitError **遮蔽** openai.RateLimitError（第 128 行 isinstance 想要 base 版，第 155/229/251 行想要 openai 版）。修复：openai 导入加别名 `RateLimitError as OpenAIRateLimitError`，155/229/251 三处同步改名。行为零变化，消除遮蔽。
- tests/unit/test_prompt.py:59 —— 同名同体测试函数重复定义（Python 后者覆盖前者，实际只跑一个）。删除第二份（59-63 行）。

### UP045/UP037 类型注解风格

- UP045 改 `X | None` 共 5 处：schemas.py:102、schemas.py:104、api/documents.py:27、eval_sany.py:38、eval_sany.py:220。
- **排除** memory.py:366 `_acquire_lock(...) -> Optional[threading.Lock]` —— `threading.Lock` 是非 type 对象不能用 `|`（AGENTS.md 已知陷阱），保留原样。
- UP037 去引号 4 处（evidence.py:55、embedding_text.py:30×2）：`--fix` 安全。
- 修完后各文件若 `Optional` 无剩余使用则从 import 行删除（schemas.py:3 等）。

### 既有缺陷（静态检查暴露，spec §0 允许修）

1. **pipeline.py:422 裸 `logger.warning` 但模块未定义 `logger`** —— evidence gate 默认关所以从未触发；一旦开 gate 且走 refuse 路径即 AttributeError。修：imports 后加 `logger = logging.getLogger(__name__)`。
2. **retrieval.py 字符串注解 `"RetrievalFilter | None"`（63/107/558 行）但从未导入 RetrievalFilter** —— 当前靠延迟求值侥幸存活，任何 get_type_hints/运行时求值即 NameError。修：retrieval.py import 区加 `from app.core.retrieval_filter import RetrievalFilter`（retrieval_filter.py 只依赖 dataclasses/typing，无循环）。

---

### Task 1: 预检

- [ ] **Step 1.1: 确认工作区干净**

Run: `cd d:/PyProject/ragent-py && git status --porcelain`
Expected: 空输出

- [ ] **Step 1.2: 记录基线失败清单**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q 2>&1 | tail -3`
Expected: `1 failed, xxx passed`（unit 内仅 test_oversized_section_packs_on_element_boundaries 一条预存失败）。记下数字，后续每个 commit 后对比。

---

### Task 2: 任务 A — 文档一致性（commit ①）

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Rewrite: `docs/TODO.md`

- [ ] **Step 2.1: CLAUDE.md — 架构快照改两层**

把 "Architecture snapshot" 里 RAG pipeline 代码块整体替换为：

```
Default Runtime Pipeline (all strategy flags off unless noted):
QueryRewrite → IntentClassify (DeepSeek-V3; route to 1-3 KBs) → Hybrid Search
(vector cosine + BM25 ts_rank + question-vector channel, RRF merge; relaxed-BM25
fallback when < top_k) → Cross-encoder Rerank → MMR diversity (λ=0.7, ≤2 per doc)
→ TopK → Prompt injection → SSE stream (TagStreamParser)

Optional Strategies (code exists, default OFF, enable via env vars — see
app/config.py; 8-group ablation showed no recall gain on the Sany corpus,
docs/plans/2026-08-23-ablation-report.md):
cross_doc / section_boost / section_supplement / year_supplement /
query_decomposition / evidence_gate
```

同文件修正：

- "LLM providers" 行：`intent: DeepSeek-R1-0528-Qwen3-8B` → `intent: DeepSeek-V3 (rewrite/decompose: DeepSeek-R1-0528-Qwen3-8B)`；`vision/OCR: DeepSeek-OCR` → `vision: Qwen/Qwen3-VL-8B-Instruct`
- 删除 "Note: The most recent cross-doc design (9db350d) supersedes..." 一句中的 commit hash 引用改为指向 plan 文档名
- "Conversation memory" 小节保留 threading.Lock 注意事项不动

- [ ] **Step 2.2: README.md — 技术栈表 + 管线图**

技术栈表两行替换：

```markdown
| 意图路由 | SiliconFlow (DeepSeek-V3)；复杂查询拆解 DeepSeek-R1-0528-Qwen3-8B |
| 视觉理解 | SiliconFlow (Qwen3-VL-8B-Instruct) |
```

"检索管线：" 图末尾追加一行注释：

```markdown
（跨文档关联为可选策略，默认关闭——ablation 显示对当前语料无 recall 收益；
env CROSS_DOC_ENABLED=true 可开启。其余可选策略见 app/config.py）
```

功能清单里 "跨文档关联检索" 条目末尾加 `（默认关闭，可配开关）`。

- [ ] **Step 2.3: AGENTS.md — 四处修正**

1. **删除 §9 Git LFS 整节**（§8 与 §10 之间的分隔线一并整理），后续章节号顺延或保留原号加 "(removed)" 注记均可，选择顺延重编号。
2. §4 LLM 栈小节整体替换为：

```markdown
### LLM 栈
- **SiliconFlow**:chat 默认 `deepseek-ai/DeepSeek-V3`;意图路由 V3;复杂查询拆解
  `DeepSeek-R1-0528-Qwen3-8B`;视觉 `Qwen/Qwen3-VL-8B-Instruct`;
  Embedding `Qwen3-VL-Embedding-8B`(4096d);Rerank `BAAI/bge-reranker-v2-m3`
- **MiniMax M3**:备选 provider(`chat_provider="minimax"` 时启用)+ metadata 批量生成
- **熔断器**:按 provider 隔离,5xx/超时/连接错才计失败,4xx 永久错误不计
```

3. §3 格式化行 `无 ruff/black/flake8 配置` → `lint/format 统一用 ruff（配置见 ruff.toml；跑 D:/miniConda/envs/rag/python.exe -m ruff check .）`。
4. §5 关键文件锚点表：行号列全部去掉，只留「文件 + 函数/符号」两列格式（如 `app/core/pipeline.py` `RAGPipeline.execute`）。CLAUDE.md 的 Key file map 保持 markdown 链接格式不动（可点击性优先），仅当链接行号明显错位时顺手校正。

- [ ] **Step 2.4: TODO.md — 整体重写**

全文替换为（这是完整新内容，直接覆盖）：

```markdown
# TODO

> 只放当前待办。已完成工作/实验记录在 docs/plans/（索引：docs/plans/README.md）。

## P0 核心正确性
- [ ] H 类（错误前提纠偏）：需要"反面证据检索"，prompt 层已到瓶颈（53.3%）
- [ ] I 类（拒答边界）：年份一致性，波动大（73.3%，需 generation eval 复验）
- [ ] C 类（跨文档对比）：根因是每年内抓错 chunk，需按年定向语义检索

## P1 工程质量（Phase 2+，按序）
- [ ] Phase 2：complexity/sub_dependencies 契约重构（prompt contract migration，须在两轮评测之间执行）
- [ ] Phase 3：Evidence Gate 接通 + 数据契约重定义（text[:300] 截断随此处理）
- [ ] Phase 4：<think>/reasoning 协议解耦（单独设计任务）
- [ ] Phase 5：Alembic 迁移收敛 init_db() 的 ALTER TABLE
- [ ] Eval 结果版本化（experiment_id/git_sha/config snapshot）
- [ ] Prompt contract 单测 + RAG contract tests（year/permission/embedding_version 过滤等）

## P2 优化
- [ ] 策略单点 ablation 复验（question channel / MMR λ / rerank top_k）

## P3 未来
- [ ] 多轮对话上下文感知检索
- [ ] 表格结构化理解
- [ ] 用户反馈闭环

## 已知技术债
- retrieval.py 852 行，待 Phase 2+ 拆包（engine/hybrid/filter/rerank/diversity）
- init_db() 内联 ALTER TABLE ×20+（见 P1 Alembic 项）
```

- [ ] **Step 2.5: 文档验收 grep**

Run:
```bash
grep -rn "DeepSeek-OCR" CLAUDE.md README.md AGENTS.md docs/TODO.md
grep -rn "LFS" AGENTS.md
grep -n "小批量验证" docs/TODO.md
```
Expected: 第一条 0 命中或仅在历史结论语境；后两条 0 命中。

- [ ] **Step 2.6: Commit ①**

```bash
git add CLAUDE.md README.md AGENTS.md docs/TODO.md
git commit -m "docs: sync architecture docs with actual defaults (Phase 1)"
```

- [ ] **Step 2.7: 快检**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q 2>&1 | tail -1`
Expected: 与 Step 1.2 数字一致。

---

### Task 3: 任务 B — 死代码（commit ②）

**Files:**
- Modify: `app/ingestion/metadata.py`

- [ ] **Step 3.1: 删 `_EMBEDDING_TEXT_PROMPT` 及残留**

删除 metadata.py 第 99-126 行整块（从 `# ── Embedding Text Enhancement ──` 到文件尾的弃用注释）。顶部 docstring 第 6-7 行删除：

```
Also provides embedding_text enhancement using a small model (Qwen2.5-7B-Instruct)
to improve retrieval quality for financial data queries.
```

同时 docstring 第 3 行 "MiniMax" 表述核对：metadata 走 `minimax_client`（config chat_provider 备选通道），保持原文即可。

- [ ] **Step 3.2: 验证零引用**

Run: `grep -rn "_EMBEDDING_TEXT_PROMPT" app/ tests/ tools/ eval/`
Expected: 0 命中。

- [ ] **Step 3.3: 验证导入链与测试**

Run:
```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q 2>&1 | tail -1
```
Expected: import 成功；unit 数字与基线一致（test_embedding_text.py 测的是 embedding_text.py 模块，不受影响）。

- [ ] **Step 3.4: Commit ②**

```bash
git add app/ingestion/metadata.py
git commit -m "chore(ingestion): remove dead _EMBEDDING_TEXT_PROMPT (Phase 1)"
```

---

### Task 4: 任务 C1-a — Ruff 安装与配置（commit ③ 的一部分）

**Files:**
- Modify: `requirements-dev.txt`
- Create: `ruff.toml`

- [ ] **Step 4.1: 安装**

Run: `D:/miniConda/envs/rag/python.exe -m pip install ruff`
确认版本：`D:/miniConda/envs/rag/python.exe -m ruff --version`

- [ ] **Step 4.2: 写配置**

创建 `ruff.toml`（完整内容）：

```toml
# Phase 1 cleanup — see docs/plans/2026-08-21-cleanup-phase1.md
line-length = 100
target-version = "py311"

[lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[lint.ignore]
# ── 规则级白名单（每条带理由；新增必须同样给理由）──

# Formatter owns wrapping; long URLs/中文长句不可机械折行。format 之后不再复评。
"E501" = "line wrapping delegated to ruff format (separate commit)"

# FastAPI dependency declaration idiom: Depends() calls factories in parameter defaults.
"B008" = "FastAPI Depends() in defaults is intentional framework pattern"

# printf-style survives in logging-adjacent code; converting to f-string changes
# lazy-evaluation semantics. Deferred — revisit in a behavior round.
"UP031" = "%-format kept: lazy logging args & eager-conversion risk"

# ── 待扫描后按决策表填充的规则级 ignore（见 Step 4.3）──
```

- [ ] **Step 4.3: 全量扫描 + 三分类 triage**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m ruff check app/ tools/ eval/ tests/ --statistics
```

对照决策表处置（本计划的完整决策程序，无自由裁量空间）：

| 类别 | 判据 | 动作 |
|---|---|---|
| 机械安全 | I001、UP037、以及 F401 清单内条目 | 进 Task 5 修复 |
| 人工判断 | B904/SIM108/SIM117/其余 UP/B/SIM 单规则命中 **≤ 20** | 逐条人工看 diff：能零语义改动就改，否则进下面 ignore |
| 白名单 | 单规则命中 **> 20** 或任何拿不准的 | 写进 `[lint.ignore]`，理由必填 |

预期高频候选（侦察时未逐规则计数，以 --statistics 输出为准）：SIM108、SIM117、B904、UP008/UP035 类。每条 ignore 的理由模板：`"<rule>" = "<为何不在本轮改：语义风险/框架惯用法/量大留行为轮>"`。

- [ ] **Step 4.4: 确认配置收敛**

Run: `D:/miniConda/envs/rag/python.exe -m ruff check app/ tools/ eval/ tests/ --statistics`
Expected: 剩余条目全部属于 Task 5 修复清单（F401/E402/F811/UP045）或已在 ignore 中。

---

### Task 5: 任务 C1-b/c — 修复（commit ③ 主体）

**Files:**
- Modify: 本计划「侦察结论」列出的全部文件 + `app/core/pipeline.py` + `app/core/retrieval.py`

- [ ] **Step 5.1: TDD — 先写 gate 回归测试（锁定 logger bug）**

创建 `tests/unit/test_evidence_gate_regression.py`（完整内容）：

```python
"""Evidence gate refuse 路径回归测试。

背景：pipeline.py 的 gate refuse 分支调用了裸 logger.warning，但该模块此前
未定义模块级 logger —— gate 开启且拒绝时即 AttributeError（Phase 1 修复，
spec 见 docs/plans/2026-08-21-cleanup-phase1.md）。本测试锁定修复不回退：
1. 模块级 logger 存在
2. gate refuse 时 SSE 序列正确终止（status → degraded → done），不再抛异常
"""
import json

from app.config import settings
from app.core.pipeline import RAGPipeline
from app.models.schemas import ChatRequest


def test_pipeline_module_defines_logger():
    """gate refuse 分支依赖模块级 logger——必须存在且可用。"""
    from app.core import pipeline as pipeline_mod

    assert hasattr(pipeline_mod, "logger"), (
        "pipeline.py 缺少模块级 logger：evidence gate refuse 路径会 AttributeError"
    )
    assert callable(pipeline_mod.logger.warning)


class _FakeGateResult:
    """绕开 organizer 内部逻辑，只测 pipeline 分支接线。"""

    coverage = 0.5
    temporal_consistent = False
    conflicts: list = []
    sources: list = [{"chunk_id": "c1", "document_id": "d1"}]
    coverage_by_year: dict = {}


async def test_gate_refuse_path_completes_without_error(monkeypatch):
    """gate 判拒答时事件流正常收尾（旧代码在此路径 AttributeError）。"""
    from app.core import pipeline as pipeline_mod

    monkeypatch.setattr(settings, "evidence_gate_enabled", True)
    monkeypatch.setattr(settings, "evidence_min_coverage", 0.99)
    monkeypatch.setattr(
        pipeline_mod.conversation_memory, "get_history", lambda cid: []
    )
    monkeypatch.setattr(
        pipeline_mod.conversation_memory, "get_summary", lambda cid: ""
    )
    monkeypatch.setattr(
        pipeline_mod.pgvector_store, "list_kb_ids", lambda s: ["kb-x"]
    )
    monkeypatch.setattr(
        pipeline_mod.retrieval_engine,
        "retrieve",
        _fake_retrieve,
    )
    monkeypatch.setattr(
        pipeline_mod.evidence_organizer, "organize", lambda **kw: None
    )
    monkeypatch.setattr(
        pipeline_mod, "build_evidence_result", lambda table: _FakeGateResult()
    )

    events: list[str] = []
    async for ev in RAGPipeline().execute(
        ChatRequest(query="测试证据门控"), user_role_ids=[1]
    ):
        events.append(ev)

    joined = "".join(events)
    assert "evidence_gate_refused" in joined
    assert "event: degraded" in joined
    assert "event: done" in joined


async def _fake_retrieve(*args, **kwargs):
    from app.models.schemas import RetrievedChunk

    return [
        RetrievedChunk(
            chunk_id="c1", document_id="d1", text="证据文本", score=0.9
        )
    ]
```

注意：写入时清掉上面 `monkeypatch.setattr(pipeline_mod.build_evidence_result, ...)` 那行怪异的三元残渣——最终版本里 mock 只需要这一行：

```python
    monkeypatch.setattr(
        pipeline_mod, "build_evidence_result", lambda table: _FakeGateResult()
    )
```

（`evidence_gate_should_refuse` 用真实实现——它接收 `_FakeGateResult` 即可判定 refuse，顺带锁住 gate 判定逻辑本身。）

- [ ] **Step 5.2: 跑新测试，确认按预期失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_evidence_gate_regression.py -v`
Expected: `test_pipeline_module_defines_logger` FAIL（no attribute 'logger'）；第二个测试 FAIL 或 ERROR。

- [ ] **Step 5.3: 修两个既有缺陷**

pipeline.py import 区（`import logging` 之后）加：

```python
logger = logging.getLogger(__name__)
```

retrieval.py import 区加（字母序插入现有 local imports 中）：

```python
from app.core.retrieval_filter import RetrievalFilter
```

- [ ] **Step 5.4: 新测试转绿**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_evidence_gate_regression.py -v`
Expected: 2 passed。

- [ ] **Step 5.5: F401 批量删除（按侦察清单逐条）**

每处删除后无需单独验证，全部完成后统一验证。注意三个特殊点：
- auth.py:10 只从 import 名单里去掉 `seed_defaults` 一个名字
- summary_concurrency.py:37 整行删
- pipeline.py:5 删掉 `RetrievalFilter` 导入（Step 5.3 已让 retrieval.py 自持）

- [ ] **Step 5.6: E402 处置**

按侦察表：doc_relation.py:42、db.py:112-113、pgvector_store.py:26/976 上移到文件头 import 区；doc_relation 原"设计审查 P2-13"注释改为 `# Shared stopword list keeps BM25/tokenization consistent across modules.` 并随导入上移。tests 三处先看代码再定（sys.path hack → noqa + 理由；否则上移）。

- [ ] **Step 5.7: F811 两处**

embedding.py:9 改 `from openai import AsyncOpenAI, RateLimitError as OpenAIRateLimitError, APIStatusError`，第 155/229 行 `except RateLimitError` 与 251 行类型注解同步改 `OpenAIRateLimitError`（128 行 isinstance 不动——它本来就该用 base 版）。test_prompt.py:59 删除重复函数体。

- [ ] **Step 5.8: I001 自动排序**

Run: `D:/miniConda/envs/rag/python.exe -m ruff check app/ tools/ eval/ tests/ --select I001,F401,I --fix`
然后人工 `git diff` 抽查 3 个文件确认纯移动无删改。

- [ ] **Step 5.9: 全量验证 + Commit ③**

```bash
D:/miniConda/envs/rag/python.exe -m ruff check app/ tools/ eval/ tests/
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q 2>&1 | tail -1
D:/miniConda/envs/rag/python.exe -c "import app.main"
git add -A
git commit -m "chore(lint): add ruff, fix unused/mispositioned imports + latent gate bugs (Phase 1)"
```

Expected: ruff 退出码 0；unit = 基线 + 2 个新测试通过；import 成功。

---

### Task 6: 任务 D — 类型风格（commit ④）

**Files:**
- Modify: `app/models/schemas.py`、`app/api/documents.py`、`eval/eval_sany.py`

- [ ] **Step 6.1: 五处 UP045 手工替换**

- schemas.py:102 `conversation_id: Optional[str] = None` → `conversation_id: str | None = None`
- schemas.py:104 `knowledge_base_ids: Optional[list[str]] = None` → `knowledge_base_ids: list[str] | None = None`
- documents.py:27 同模式替换
- eval_sany.py:38、:220 同模式替换
- 各文件替换后若无剩余 `Optional[` 使用 → 从 typing import 中移除

**禁止越界**（spec §5）：不改默认值、不改字段类型本体、memory.py:366 的 `Optional[threading.Lock]` 保留。

- [ ] **Step 6.2: 验证**

```bash
grep -rn "Optional\[" app/ eval/ tools/ --include="*.py" | grep -v "threading.Lock"
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q 2>&1 | tail -1
```
Expected: grep 0 命中；unit 数字不变。

- [ ] **Step 6.3: Commit ④**

```bash
git add -A && git commit -m "refactor(types): Optional[X] -> X | None (Phase 1)"
```

---

### Task 7: 任务 E — 注释去日志化（commit ⑤）

**Files:** `app/config.py`、`app/core/retrieval.py`、`app/core/pipeline.py`、`app/store/pgvector_store.py`、`app/ingestion/indexer.py`、`app/core/retrieval_filter.py`、`app/core/evidence.py`、`app/ingestion/embedding_text.py`、`app/core/doc_relation.py`

- [ ] **Step 7.1: 全量定位**

Run: `grep -rn -E "Day [0-9]|上午|下午|plan §|设计审查|已弃用|回滚到|历史：" app/ --include="*.py"`

- [ ] **Step 7.2: 逐条改写（保留原则：只留“是什么+为什么”，删“何时做的+谁定的+试过什么”）**

标准改写示例（照此风格处理全部命中）：

config.py 策略旗标块 →

```python
    # Retrieval strategies default off: the 8-group ablation showed no recall gain
    # and slightly negative MRR on the Sany corpus
    # (docs/plans/2026-08-23-ablation-report.md). Env vars keep them available.
```

config.py embedding version 块 → 保留"v1/v2 含义 + 如何切换 env + 工具命令"这些操作性信息，删"Day 2 上午""2026-08-21 重跑经过"，结论句保留一条 ablation 报告引用。

indexer.py:283-287 →

```python
            # Production embeds c.text directly: the build_embedding_text() prefixes
            # measurably hurt retrieval quality in ablation
            # (docs/plans/2026-08-23-ablation-report.md). Kept for a future redesign.
```

retrieval_filter.py docstring → 删 Day/plan 引用，保留"frozen 快照可哈希作 cache key""None 与空集合语义不同"这两条真正的设计依据。

- [ ] **Step 7.3: 验收 grep**

Run: `grep -rn -E "Day [0-9]|plan §|已弃用|设计审查" app/ --include="*.py"`
Expected: 0 命中（"上午/下午/历史"类逐条判断后也应清零）。

- [ ] **Step 7.4: 验证 + Commit ⑤**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q 2>&1 | tail -1
git add -A && git commit -m "chore(comments): replace dev-diary comments with design rationale (Phase 1)"
```

---

### Task 8: 任务 C2 — ruff format（commit ⑥，最后一个语义隔离 commit）

- [ ] **Step 8.1: 执行**

```bash
D:/miniConda/envs/rag/python.exe -m ruff format app/ tools/ eval/ tests/
```

- [ ] **Step 8.2: 抽查 diff 无语义变化**

Run: `git diff --stat | tail -5` 和随机抽 2 个文件的 `git diff`
Expected: 仅空白/引号/换行/尾逗号变化；出现任何非格式 diff → `git checkout .` 回退排查。

- [ ] **Step 8.3: Commit ⑥**

```bash
git add -A && git commit -m "style: ruff format (Phase 1, no semantic changes)"
```

---

### Task 9: 最终验收

- [ ] **Step 9.1: 全量测试对齐不可变基线**

```bash
D:/miniConda/envs/rag/python.exe -m pytest -q 2>&1 | tail -3
```
Expected: `6 failed, 459 passed, 13 skipped`（457 基线 + 2 个新 gate 回归测试）；失败清单不多不少同为那 6 条。

- [ ] **Step 9.2: 导入链 + ruff 终检**

```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
D:/miniConda/envs/rag/python.exe -m ruff check app/ tools/ eval/ tests/
D:/miniConda/envs/rag/python.exe -m ruff format --check app/ tools/ eval/ tests/
```
Expected: 全部退出码 0。

- [ ] **Step 9.3: 文档/注释验收复核**

重跑 Task 2 Step 2.5 与 Task 7 Step 7.3 的 grep。
Expected: 全部 0 命中或符合豁免语境。

- [ ] **Step 9.4: 会话收尾**

按 CLAUDE.md Session workflow 更新 docs/plans/ 下一步文档 + TODO.md（若执行中有新发现）。不重跑评测——最终回归声明直接引用本计划头部基线数字。

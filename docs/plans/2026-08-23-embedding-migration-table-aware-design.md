# 设计文档：RAG 本体升级 v2 — Embedding 维度迁移 + 表格感知摄入（Issue #2 第一期）

> 日期：2026-08-23
> 状态：设计已获用户批准（含 4 项修正）；实施计划另行成文
> 关联：GitHub [acsRef/Rag#2](https://github.com/acsRef/Rag/issues/2)（table-aware chunking）· Evaluator v1 LOCKED (`416cff2`) · [2026-08-23-next-steps.md](2026-08-23-next-steps.md)

## 0. 术语纪律

4096→1024 一律称为 **embedding 输出维度配置迁移**（output dimension configuration change）。1024 是 Qwen3-Embedding-8B 经 SiliconFlow API 明确支持的合法输出维度之一（64/128/256/512/768/1024/1536/2048/2560/4096），**不得表述为"降维"、不得预设其效果变差或变好**——由 Baseline-2 实测判定。

## 1. 背景

三个事实触发本设计：

1. **Evaluator v1 LOCKED**（judge prompt / rubric / collapse 冻结，改动需 v2 + 重校准）——首次具备可信测量 RAG 本体变化的能力。
2. **Issue #2** 提出 table-aware chunking 四块：原始表示保留 / 归一化检索表示 / 表格 metadata / 表格感知检索策略。
3. **关键隐藏变量**：dev 库 `chunk_questions = 0`——历史语料摄入早于 question channel 功能。因此：

   > ⚠️ **Baseline-1（历史 78.3% / V3 10 题 60%）的 question channel 是"配置开启但数据缺失的历史状态"，不是一个有效的 question-channel baseline。** 未来读配置时不得误以为 Baseline-1 已包含该能力。

本次借用户决定的"清库重插"窗口，一次性完成两个本体升级，且保持单变量归因纪律（[eval-methodology-feedback](../../C:\Users\Lenovo\.claude\projects\d--PyProject-ragent-py\memory\eval-methodology-feedback-2026-08-23.md)：复合变更 ≠ 单变量归因）。

## 2. 决策记录（用户逐项确认 2026-08-23）

| 决策点 | 结论 |
| --- | --- |
| 变量隔离 | 分步：Step 1 纯 embedding → **Baseline-2**；Step 2A 表格 schema + question channel → **Baseline-3**；Step 2B 大表摘要独立实验 |
| 物理迁移方式 | 直接换列维度 + 清库重插（不做双列共存；旧 4096 向量无保留价值） |
| 新 embedding 模型 | `Qwen/Qwen3-Embedding-8B` @ `dimensions=1024`（中文财报语料，同 Qwen 家族，SiliconFlow 直供） |
| Issue #2 范围 | 摄入层先行（2A），表格检索策略二期 |
| 清理范围 | 语料侧 + 对话侧全清；users / roles / KB / PII 规则保留 |
| 评测节奏 | 每步 15 题 verified 快测把关，两步绿后跑 65 题全量正式 benchmark（locked evaluator v1） |
| `db.py` 维度单点驱动 | **必做的工程修复**（configuration contract：config → embedding client → DB schema 一条线），不算实验变量 |

用户修正采纳（4 项）：

1. **API 显式传 `dimensions`**——否则 config=1024 / DB=1024 但 API 实际返回维度不确定，单点驱动成假闭环。
2. **EmbeddingCache key 加 model + dimension**——现 key 仅文本哈希，换模型后命中旧缓存返回错误维度向量，属 correctness bug。
3. **回滚定义 = re-run migration + full re-index**，不是 `.env` 两行（涉及 DB schema / cache / API 参数三层）。
4. **Step 2 拆 2A/2B**——行展开归一化与 LLM 大表摘要不同批上线，避免 Baseline-3 又成复合变更。

## 3. Step 1 — Embedding 迁移（产出 Baseline-2）

### 3.1 变量定义

```
Step 1 变更集 = {
  embedding_model : Qwen/Qwen3-VL-Embedding-8B → Qwen/Qwen3-Embedding-8B
  dimension       : 4096 → 1024（API 显式传参 + DB schema + cache key 三处同步）
  question channel: 显式 QUESTION_CHANNEL_ENABLED=false（对齐 Baseline-1 实际行为）
}
不变量           = chunker / hybrid_search / rerank / MMR / prompt / evaluator v1
```

### 3.2 代码变更清单

**(a) [app/config.py](../../app/config.py)**

```python
embedding_model: str = "Qwen/Qwen3-Embedding-8B"
embedding_dimension: int = 1024
embedding_send_dimensions: bool = True   # env: EMBEDDING_SEND_DIMENSIONS
# 说明：Qwen3-Embedding 系列接受 dimensions 参数；bge-m3 等固定维度模型不支持，
# 切换此类模型时置 false（此时其原生维度须写入 embedding_dimension）。
```

**(b) [app/llm/embedding.py](../../app/llm/embedding.py)** — 三处调用显式传参：

```python
kwargs = {"model": self.model, "input": text}
if settings.embedding_send_dimensions:
    kwargs["dimensions"] = settings.embedding_dimension
resp = await self.client.embeddings.create(**kwargs)
```

覆盖：`embed()`、`embed_single_chunk()`、`_try_batch_with_retry()`。契约：**API 返回维度 ≡ settings.embedding_dimension**，违反即配置错误，应在冒烟阶段暴露。

**(c) [app/core/cache.py](../../app/core/cache.py) EmbeddingCache key 加固（correctness fix）**

```python
@staticmethod
def _key(model: str, dimension: int, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{dimension}\x00{text}".encode("utf-8")).hexdigest()
```

调用方（`SFEmbedding.embed` / `embed_single_chunk`）同步传入。注：缓存为进程内 LRU，重启即清——迁移本身靠重启已安全；此加固防的是**同一进程内**切换配置的错配，为长期正确性。

**(d) [app/store/db.py](../../app/store/db.py) — 受控修改（CLAUDE.md "do not modify" 条款本次有意豁免，理由见决策表）**

4 处硬编码改由 settings 驱动：raw SQL `"embedding VECTOR({dim})"`（L143）+ `Vector(...)` ×3（chunks L306 / chunk_questions L289 / doc_embeddings L432）→ `Vector(settings.embedding_dimension)` / f-string 同源。

**(e) 迁移脚本 `tools/migrate_vector_dimension.py`（幂等、可复用）**

先检测再动刀，不假设三表状态一致：

```text
for table in (chunks, chunk_questions, doc_embeddings):
    cur_dim = SELECT atttypmod 解析 vector 维度
    rows   = count(*)
    if cur_dim == target: skip
    if rows > 0: ABORT（要求先 TRUNCATE——本流程保证空表 ALTER）
    ALTER TABLE ... ALTER COLUMN embedding TYPE vector(:target)
```

检测逻辑可复用于未来任何维度迁移（如 1024→1536）。

**(f) 测试适配**

- `tests/integration/test_cross_doc.py:136`、`test_search_errors.py:17/66`：`[0.1] * 4096` → `[0.1] * settings.embedding_dimension`
- `tests/integration/test_ingestion.py:18`：`== 4096` → `== settings.embedding_dimension`
- `conftest.fake_vector` 已 `_DIM = settings.embedding_dimension`，无需动
- `app/core/doc_relation.py:504` 注释去掉 4096 字样
- 新增单测：EmbeddingCache key 含 model/dimension（不同 model 或 dim 不互 hit）

### 3.3 执行序列（顺序即正确性）

```
1. curl 冒烟 SiliconFlow POST /v1/embeddings
   {model: "Qwen/Qwen3-Embedding-8B", input: "测试", dimensions: 1024}
   → 确认返回 len(vec)==1024 后才允许进入后续步骤
2. 停后端（uvicorn）
3. TRUNCATE 清库（见 §6 清单）
4. python tools/migrate_vector_dimension.py --target 1024   # 空表瞬时完成
5. .env / config 更新（model + dimension + QUESTION_CHANNEL_ENABLED=false）
6. 启动 → import chain check → pytest 全量守口
7. API 上传三份年报 PDF（eval/sany_annual_reports/*.pdf）全新摄入
8. 抽查：SELECT count(*) FROM chunks;  向量维度抽检；
   chunk_questions 仍应为 0（channel off）
9. eval 15 题 verified 快测 → Baseline-2 锁定文档入 docs/plans/
```

### 3.4 通过标准（不预设分数）

- pytest：546 passed / 6 failed / 13 skipped 基线守口（6 failed 集合禁止新增）
- 15 题快测 vs Baseline-1：总分不低于 −10pp 且无单一类别系统性崩塌（如 A 类从满分掉一半）
- 达标 → 锁定 **Baseline-2**；不达标 → 记录结果，回滚（§8），分析后再议

## 4. Step 2A — 表格感知摄入第一期（产出 Baseline-3）

### 4.1 双表示映射（Issue #2 §1/§3 落地）

```text
                TABLE (parser 产出的 Markdown 表格块)
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
   Generation 通道              Retrieval 通道
   chunks.text                  embedding_text + search_text
   保持原始 Markdown             行展开自然语言归一化
   （结构无损）                 （"2023年营业收入100M；2024年…"）
```

- **原始表示**：`chunks.text` 不动，Markdown 表格原样进 prompt（生成期列对齐信息保留）。
- **归一化表示**：表格行展开为自然语言短句，写入既有 `embedding_text` 列并 embed 它；同时写入 `search_text` 供 BM25 词面命中（"每股分红"类查询）。
- **metadata**（Issue #2 §3，本期只入库不消费，为二期 table-only retrieval 铺路）：新增列 `chunk_type VARCHAR(16)` / `table_headers TEXT[]` / `table_meta JSONB`——走 init_db 既有 `ADD COLUMN IF NOT EXISTS` 幂等模式（year/page 列先例）。

### 4.2 明确边界

- **复用 mechanism，不复用既有 v2 conclusion**：`build_embedding_text()` 作为"增强文本进 embedding_text 列"的机制复用；此前 v2 ablation 的"MRR 下降"结论针对 document/section prefix 噪声，与表格行展开是完全不同的语义表示，**不构成对本设计的预测**——由 Baseline-3 快测重新验证。
- **检索算法零改动**：hybrid_search / rerank / MMR / RRF 权重全部不动。
- **question channel ON**：随新摄入管线激活（`QUESTION_CHANNEL_ENABLED=true` 回默认），LLM 逐 chunk 生成候选问题进 `chunk_questions`。它与表格 schema 同属"新 ingestion package"，合并为一个 Step 是有意决策。
- **小表现状保留**：现有 `_clean_table_text` 小表转自然语言的逻辑与新行展开机制的关系在实施时统一（预计：小表直接自然语言化进 text，大表才走双表示——具体阈值实施 plan 细化）。

### 4.3 通过标准

- B 类（表格理解与单位换算，6 题）方向性改善；其余类别不塌（对照 Baseline-2）
- C 类（跨文档对比，8 题）观察项：行展开是否改善年度内抓错 chunk 问题
- 达标 → 锁定 **Baseline-3**

## 5. Step 2B — 大表 LLM 摘要（独立实验，2A 之后）

仅对超过行数阈值的表生成 LLM 摘要，拼接于 embedding_text 头部（Issue #2 §2）。存 `chunks.summary`。单独 baseline 对比 2A，验收同纪律。**不与 2A 同批上线。**

## 6. 清库与重插清单

```text
TRUNCATE（语料 + 对话）            保留
├── chunks (1381)                  ├── users / roles / role_permissions / user_roles
├── chunk_questions (0)            ├── knowledge_bases / kb_role_access
├── documents (3)                  ├── sensitive_rules
├── doc_role_access                ├── pii_alerts / pii_hold（本就为空）
├── doc_embeddings (3)             ├── checkpoints / checkpoint_* （空）
├── doc_relations (6)              └── dim_* / fact_*（数仓演示表，与 RAG 无关，不动）
├── doc_entities (600)
├── conversations (1539)
└── messages (2655)

doc_role_access 是 documents 的权限关联表，随语料一起清；
重插后由上传流程按 KB 可见性自动重建。
```

执行方式：迁移脚本内置 TRUNCATE 步骤（带 `--yes` 确认旗标），或独立 SQL——实施 plan 定。重插经 `/api/v1/documents` 正常上传路径（走完整 ingestion + PII 过滤 + hash 复用逻辑）。

## 7. 评测纪律（locked evaluator v1 全程不动）

| 步骤 | 评测 | 通过标准 | 产物 |
| --- | --- | --- | --- |
| Step 1 完成 | 15 题 verified 快测 | ≥ Baseline-1 −10pp，无类别系统性崩塌 | Baseline-2 锁定 doc |
| Step 2A 完成 | 15 题 verified 快测 | B 类改善 + 其余不塌 | Baseline-3 锁定 doc |
| 两步绿后 | 65 题全量 benchmark | acceptance criteria 如实记录，不预设分数目标 | 正式 benchmark 报告 |

每步独立 commit + plan 文档；快测与历史 baseline 同题集同 evaluator；任何一步不过即停，不带病进下一步。

## 8. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| SiliconFlow `dimensions=1024` 实际不可用 | 执行序列第 1 步 curl 冒烟前置；失败则评估 bge-m3（固定 1024）或维持 4096 只做 Step 2A |
| 1024 实测掉点超阈 | 回滚 = config 回退 + re-run migration --target 4096 + full re-index（三层联动，非 `.env` 两行） |
| 行展开引入噪声伤及非表格检索 | 双表示隔离：generation 通道原文无损；15 题快测把关；`embedding_text` 为 nullable 列，可按 chunk 粒度回退 |
| 动 db.py 违反项目约束 | 本设计即为约束修订场景：迁移脚本幂等 + 先检测后 ALTER + 测试守口；plan 与 commit message 显式声明 |
| 摄入中断留下半成品索引 | indexer 已有"全失败保旧索引"语义，但本次是全新库——中断即重跑上传，无旧索引保护需求 |

## 9. Non-goals

- ✗ 表格检索策略（table-only retrieval / table-aware rerank）→ 二期（metadata 本期只入库）
- ✗ Evidence Gate（KEEP DISABLED / DEFER 维持）
- ✗ Evaluator v1 任何改动（citation deterministic check 等属 evaluator v2）
- ✗ ANN 索引引入（仍暴力扫描；维度变更因此无需重建索引）
- ✗ Alembic 收敛（P1 既有独立 TODO）
- ✗ prompt / reranker / MMR / RRF 权重等任何检索参数调整

## 10. Baseline 谱系（维护用）

```text
历史基线   65题 78.3%（三轮优化终点）/ Baseline-1 V3 10题 60%
             ↑ question channel 配置开而数据缺（无效能力）
Baseline-2 = Step 1 后（1024 + Qwen3-Embedding-8B，channel off）
Baseline-3 = Step 2A 后（表格 schema + channel on）
Baseline-4… = Step 2B 及后续实验
```

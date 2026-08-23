# 设计文档：RAG 本体升级 v2 — Embedding 配置迁移 + 表格感知摄入（Issue #2 第一期）

> 日期：2026-08-23（v2 修订：吸收用户二轮审查 4×P0 + 6×P1）
> 状态：设计修订版待用户终审；实施计划另行成文
> 关联：GitHub [acsRef/Rag#2](https://github.com/acsRef/Rag/issues/2) · Evaluator v1 LOCKED (`416cff2`) · [2026-08-23-next-steps.md](2026-08-23-next-steps.md)

## 0. 术语与实验语义纪律

1. 本次变更称 **embedding configuration migration**（嵌入配置迁移）——同时变更 model 与 output dimension 两项配置：

   ```text
   Qwen/Qwen3-VL-Embedding-8B @ 4096   →   Qwen/Qwen3-Embedding-8B @ 1024
   ```

   **Baseline-2 测量的是"替换整个 embedding 配置"的效果，不是 output dimension 的孤立效果。** 不得表述为"降维"，不得预设 1024 更差或更好。

2. 维度合法性表述（保守口径）：Qwen3-Embedding-8B 官方支持 **32–4096 自定义输出维度（MRL）**；本项目选择 1024。SiliconFlow API 的 `dimensions` 参数透传以执行序列第 1 步冒烟实测为准，不依赖文档枚举值。
3. **单变量归因纪律贯穿所有 baseline**：每个 baseline 只允许一个变更簇（见 §2 谱系）。

## 1. 背景

1. **Evaluator v1 LOCKED** ——首次具备可信测量 RAG 本体变化的能力。
2. **Issue #2** 四块诉求：原始表示保留 / 归一化检索表示 / 表格 metadata / 表格感知检索策略。
3. **隐藏变量一（历史）**：dev 库 `chunk_questions = 0`，历史语料摄入早于 question channel 功能。
   > ⚠️ **Baseline-1 的 question channel 是"配置开启但数据缺失的历史状态"，不是有效的 question-channel baseline。**
4. **隐藏变量二（本次代码核查发现）**：`QUESTION_CHANNEL_ENABLED` 只控制检索侧消费（[pgvector_store.py:498](../../app/store/pgvector_store.py#L498)）；摄入侧 question 生成+向量入库是无条件执行的（[indexer.py:461-489](../../app/ingestion/indexer.py#L461-L489)）。**仅置 flag=false 不能使 `chunk_questions=0`**——必须给摄入侧加同一开关的门控，硬断言才可达成。
5. **实现断层（本次代码核查发现）**：
   - `build_embedding_text()` 是 metadata prefix builder（文档/章节/表格标题前缀，[embedding_text.py](../../app/ingestion/embedding_text.py)），**没有表格行展开能力**；
   - 生产路径 embed 的输入是 `c.text`（[indexer.py:298](../../app/ingestion/indexer.py#L298)），`embedding_text` 列当前只存 `c.text` 作审计；
   - `add_chunks()`（新文档路径，[pgvector_store.py:44-60](../../app/store/pgvector_store.py#L44-L60)）丢弃 `embedding_text` 字段，而 `replace_chunks()`（增量路径，[pgvector_store.py:606](../../app/store/pgvector_store.py#L606)）会写——两路径行为不一致。

   因此表格归一化表示是**新建函数 + 打通写入链路**的开发任务，不能默认"复用现有函数即完成"。
6. 用户决定借清库重插窗口一次性完成升级；清理由用户主动要求（chunk + 对话等全清后重插）。

## 2. Baseline 谱系（修订版）

```text
Historical Baseline — 65q 78.3% / V3 10q 60%（旧 evaluator、旧管线；仅作历史参考，不再直接比较）

Baseline-1R  replay：旧配置原库重放（见 §3，先于一切破坏性操作）
             15 verified · evaluator v1 · 旧模型@4096 · channel 实际空转
     ↓ 唯一变量：embedding 配置（model + dimension 同步迁移）
Baseline-2   Qwen3-Embedding-8B @1024 · channel OFF
     ↓ 唯一变量：表格感知摄入 schema（归一化检索表示 + metadata）
Baseline-3   table-aware · channel OFF
     ↓ 唯一变量：question channel 激活（摄入生成 + 检索消费）
Baseline-4   question channel ON
     ↓ 唯一变量：大表 LLM 摘要
Baseline-5   large-table summary（独立实验，排期另议）

之后全部实验（B2 DELETE / Table-aware 检索策略二期 / model routing / MCP /
65 题正式 benchmark）统一以最新 locked baseline 为对照。
```

每级 delta 单独归因；**Baseline-3 不再声称测"table-aware + channel"复合包**。

## 3. Phase 0 — Baseline-1R replay（先于一切破坏性操作）

**目的**：修复比较口径。Baseline-1 只有 10 题结果，与 15 题 verified 子集不同 population，不可直接 Δ。必须在旧库、旧配置上用 locked evaluator v1 重放 15 题，得到同口径参照点。

```text
前置条件：dev 库保持现状（旧向量/旧 chunks/chunk_questions=0）、.env 保持旧配置
动作    ：eval 工具跑 15 verified 题（human_verified_15.json 子集）× evaluator v1
产出    ：docs/plans/ Baseline-1R 结果文档（15q / evaluator-v1 / 旧配置）
完成判据：结果落盘后才允许进入 §4 的任何 TRUNCATE / ALTER
```

注：channel 配置保持原样不刻意关——库里本无问题数据，有效行为即 OFF，忠实反映 Baseline-1 实际状态。

## 4. Step 1 — Embedding 配置迁移（→ Baseline-2）

### 4.1 变量定义

```text
变更集 = {
  embedding_model : Qwen3-VL-Embedding-8B → Qwen3-Embedding-8B
  dimensions      : 4096 → 1024（API 显式传参 + DB schema + cache key 三处同步）
  question channel: 摄入侧 + 检索侧双门控 OFF（对齐 Baseline-1 有效行为）
}
不变量 = chunker / hybrid_search / rerank / MMR / prompt / evaluator v1
```

### 4.2 代码变更清单

**(a) [app/config.py](../../app/config.py)**

```python
embedding_model: str = "Qwen/Qwen3-Embedding-8B"
embedding_dimension: int = 1024
embedding_send_dimensions: bool = True   # env: EMBEDDING_SEND_DIMENSIONS
# bge-m3 等固定维度模型不支持 dimensions 参数，切换时置 false，
# 其原生维度须写入 embedding_dimension。
```

**(b) [app/llm/embedding.py](../../app/llm/embedding.py)** — 三处调用点显式传参：

```python
kwargs = {"model": self.model, "input": text}
if settings.embedding_send_dimensions:
    kwargs["dimensions"] = settings.embedding_dimension
resp = await self.client.embeddings.create(**kwargs)
```

覆盖 `embed()` / `embed_single_chunk()` / `_try_batch_with_retry()`。契约：**API 返回维度 ≡ settings.embedding_dimension**，违反即配置错误，由冒烟步暴露。

**(c) [app/core/cache.py](../../app/core/cache.py)** — EmbeddingCache key 版本化（correctness fix）：

```python
key = sha256(f"{model}\x00{dimension}\x00{input_version}\x00{text}")
```

新增模块常量 `EMBEDDING_INPUT_VERSION`（初始 1；§5 表格归一化上线时 bump 到 2）。防同进程换配置后旧维度向量被命中；version 显式化让表示变更可诊断。

**(d) 摄入侧 question 门控**（隐藏变量二的修复）：[indexer.py](../../app/ingestion/indexer.py) 的 question embed + upsert 块加 `if settings.question_channel_enabled:` 门控。flag off → 不生成问题向量、不写 `chunk_questions`（metadata 生成的 title/summary/questions 文本不受影响，仅跳过向量通道落库）。离线单测覆盖两种 flag 状态。

**(e) [app/store/db.py](../../app/store/db.py)** — 受控修改（CLAUDE.md 条款本次有意豁免，commit 显式声明）：

- **ORM 与 raw SQL 必须同步改**：`CREATE TABLE` 内联 SQL 的 `VECTOR(4096)`（L143）+ `Vector(4096)` ×3（chunks / chunk_questions / doc_embeddings）→ 全部由 `settings.embedding_dimension` 驱动。漏改任一侧都会造成 fresh DB 与 migrated DB 环境漂移。
- 新增表格 metadata 列（§5 用，此处一并建）：`chunk_type VARCHAR(16)` / `table_headers TEXT[]` / `table_meta JSONB`，走既有 `ADD COLUMN IF NOT EXISTS` 幂等模式。

**(f) 迁移脚本 `tools/migrate_vector_dimension.py`**（幂等、只管 schema，不管数据销毁）：

- 维度检测用 `format_type(a.atttypid, a.atttypmod)` 读出 `vector(4096)` 再解析数字，**不解析 typmod 魔数**；
- 对 chunks / chunk_questions / doc_embeddings 三表逐一检测 cur_dim 与行数；已是目标维度则 skip；**行数 > 0 则 ABORT**（要求先跑 reset 脚本）；
- 可复用于未来任意维度迁移（如 1024→1536）。

**(g) 清库脚本 `tools/reset_rag_corpus.py`**（数据销毁与 schema 迁移职责分离）：

- 仅 TRUNCATE 明确列出的表清单（§7），**不用无边界 CASCADE**；单条 TRUNCATE 多表语句内列出全部互引用表即可免 CASCADE；
- `RESTART IDENTITY` 显式开启（id 从 1 重排，诊断 artifact 可读性优先；对 RAG 正确性无影响）；
- 强制 `--yes` 旗标确认，默认 dry-run 打印将清空的对象与行数。

**(h) 测试适配**

- `[0.1] * 4096` 硬编码 → `settings.embedding_dimension`（test_cross_doc / test_search_errors ×2）；test_ingestion 维度断言参数化；
- conftest `fake_vector` 已参数化不动；doc_relation 注释去 4096 字样;
- 新增单测：cache key 隔离（model/dim/version 不同不互 hit）、摄入侧 question 门控、migrate/reset 脚本的检测逻辑（mock DB 层）。

### 4.3 执行序列（顺序即正确性）

```text
 0. Phase 0 完成（Baseline-1R 已落盘）
 1. curl 冒烟 SiliconFlow /v1/embeddings {model, input:"测试", dimensions:1024}
    → len(vec)==1024 才允许继续；失败则停（备选见 §9）
 2. 停后端
 3. python tools/reset_rag_corpus.py --yes        # 语料+对话全清
 4. python tools/migrate_vector_dimension.py --target 1024   # 空表瞬时
 5. .env / config 更新（model + dimension；QUESTION_CHANNEL_ENABLED=false）
 6. 启动 → import chain check → pytest 全量守口
 7. API 上传三份年报全新摄入（走完整 ingestion + PII + hash 逻辑）
 8. Index Integrity Report（§8，含 chunk_questions==0 硬断言）
 9. eval 15 verified 快测 → 对比 Baseline-1R → Baseline-2 锁定文档
```

### 4.4 通过标准（不预设分数）

- pytest：`546 passed / 6 failed / 13 skipped` 守口（6 failed 集合禁止新增）
- 15 题 vs **Baseline-1R**（同 population 同 evaluator）：总分不低于 −10pp，且无单一类别系统性崩塌
- 达标 → 锁定 **Baseline-2**；否则记录、回滚（§10）、分析后再议

## 5. Step 2A — 表格感知摄入第一期（→ Baseline-3）

### 5.1 双表示架构（Issue #2 §1/§3 落地）

```text
              TABLE（parser 产出的 Markdown 表格块）
                    │
      ┌─────────────┴──────────────┐
      ▼                            ▼
 Generation 通道               Retrieval 通道
 chunks.text                   chunks.embedding_text + search_text
 原始 Markdown 原样保留          归一化文本
 （列对齐结构无损进 prompt）
```

**新建纯函数 `build_retrieval_text(chunk, doc)`**（[app/ingestion/](../../app/ingestion/)，与 `build_embedding_text` 平级、各司其职）：

```text
普通文本 chunk：retrieval_text = c.text（维持现状，不加前缀）
table chunk  ：retrieval_text =
                 表头行（"指标 | 2023年 | 2024年"）
                 + 每行独立自包含的自然语言句
                 （"2024年营业收入为150亿元，同比增长10%。"）
                 —— 不是"2024 | 150亿 | 10%"的管道拼接；
                    行句子须带行首实体（年份/科目），脱离表头仍可独立匹配
metadata 列  ：chunk_type="table"、table_headers=[...]、table_meta={row_range, page,...}
```

- `build_embedding_text()`（prefix builder）**保留现状不动**，供 ablation 工具继续使用；两者语义不同，不让一个函数背两个职责。
- 写入链路打通：indexer 对 table chunk 以 `retrieval_text` 作为 **embed 输入**并写入 `embedding_text` 列（此时该列语义升级为"实际 embedding 输入"）；`search_text` 同步取 `tokenize(retrieval_text)` 让 BM25 词面命中（"每股分红"类查询）。**`add_chunks()` 补写 `embedding_text` 字段，消除与 `replace_chunks()` 的路径不一致。**
- `EMBEDDING_INPUT_VERSION` bump → 2（cache key 联动）；新语料 `embedding_version` 统一标 3（1=裸 text 历史 / 2=prefix build 历史 / 3=retrieval-text build），`CURRENT_EMBEDDING_VERSION=3`——全库统一重插，过滤语义自洽。
- 小表现状（`_clean_table_text` ≤4 行转自然语言进 text）：与新机制的关系在实施 plan 统一——预计小表维持现行为、大表走双表示，阈值实施时定。
- **复用 mechanism，不复用既有 v2 conclusion**：此前"MRR 下降"结论针对 document/section prefix 噪声，与本设计的表格行展开是不同的语义表示，不构成预测——由 Baseline-3 快测重新验证。

### 5.2 明确边界

- **检索算法零改动**：hybrid_search / rerank / MMR / RRF 权重全不动；metadata 本期只入库不消费（二期 table-only retrieval 铺路）。
- **question channel 保持 OFF**（摄入侧+检索侧）：`chunk_questions == 0` 硬断言继续成立。

### 5.3 通过标准

| 类别 | 性质 | 标准 |
| --- | --- | --- |
| B 类（表格理解与单位换算，6 题） | **Gate** | 相对 Baseline-2 **不得下降** |
| B 类 | Target | 期望改善（改善幅度不作 pass 条件） |
| 其余类别合计 | Gate | 不低于 Baseline-2 −10pp 且无类别系统性崩塌 |
| C 类（跨文档对比） | 观察项 | 记录行展开对"年度内抓错 chunk"的影响，不做 gate |

达标 → 锁定 **Baseline-3**。

## 6. Step 2C — Question channel 激活（→ Baseline-4）

唯一变量：`QUESTION_CHANNEL_ENABLED=true`（摄入侧生成 + 检索侧消费同步激活）。

```text
1. 删除三份文档（API delete）→ 重新上传（绕过 hash 复用强制全量重建，
   使 chunk_questions 真正有数据——增量路径不会为复用 chunk 回填问题向量）
2. Index Integrity Report：chunk_questions > 0 硬断言 + 问题向量维度抽检
3. eval 15 verified 快测 → 对比 Baseline-3 → Baseline-4 锁定文档
```

通过标准：总分不低于 Baseline-3 −10pp、无类别崩塌；question channel 的 RRF 权重等参数调优属后续实验，本期只用默认 0.15。

## 7. 清库清单（Phase 0 之后、迁移之前执行一次）

```text
TRUNCATE ... RESTART IDENTITY（语料 + 对话；单语句多表，不用 CASCADE）：
  messages / conversations / checkpoint_blobs / checkpoint_writes / checkpoints
  chunk_questions / chunks / doc_role_access / documents
  doc_embeddings / doc_entities / doc_relations

保留：
  users / roles / role_permissions / user_roles
  knowledge_bases / kb_role_access
  sensitive_rules / pii_alerts / pii_hold（后两者本为空）
  dim_* / fact_*（数仓演示表，与 RAG 无关）
```

checkpoints 系列属对话侧状态（LangGraph 会话 checkpoint），当前为空表，一并纳入 TRUNCATE 清单防遗留。

doc_role_access 是 documents 的权限关联表随语料清；重插后由上传流程按 KB 可见性重建。

## 8. Index Integrity Report（每次 re-index 后必跑）

```text
documents            == 3
chunks               > 0
embedding 维度抽样    == settings.embedding_dimension
embedding NULL 数     == 0
embedding_text       null ratio / avg length / table chunk 覆盖率
search_text          空 ratio == 0
chunk_questions      Phase Step1/2A == 0（硬断言）; Step 2C > 0（硬断言）
问题向量维度抽样       == settings.embedding_dimension
```

硬断言失败的哲学：**不允许再出现"配置开了但数据没进来"（或反向）的静默错配**——本次发现的两个隐藏变量都属于此类。

## 9. 评测纪律与风险

| 步骤 | 对照 | Gate | 产物 |
| --- | --- | --- | --- |
| Phase 0 | — | — | Baseline-1R 文档 |
| Step 1 | Baseline-1R | ≥−10pp 且无类别崩塌 | Baseline-2 文档 |
| Step 2A | Baseline-2 | §5.3 表 | Baseline-3 文档 |
| Step 2C | Baseline-3 | ≥−10pp 且无类别崩塌 | Baseline-4 文档 |
| 最终 | 最新 baseline | acceptance criteria 如实记录 | **65 题正式 benchmark**（overall + 分类别 + answerable/refusal + numeric/table/cross-doc 分面报告，防收益被 overall 稀释） |

15 题快测是 regression gate 不是最终统计；65 题才是正式结论。

| 风险 | 缓解 |
| --- | --- |
| SiliconFlow dimensions=1024 不可用 | 冒烟前置；失败则评估 bge-m3（固定 1024，`send_dimensions=false`）或维持 4096 先做 Step 2A |
| 新配置实测掉点超阈 | 回滚 = config 回退 + migrate --target 4096 + reset + full re-index（三层联动，非 `.env` 两行） |
| 行展开引入噪声 | 双表示隔离（generation 原文无损）；embedding_text 按 chunk 粒度可回退；快测把关 |
| 动 db.py 违反约束 | 有意豁免场景：脚本幂等 + 先检测后 ALTER + 测试守口 + commit 显式声明 |
| 摄入中断 | 全新库无旧索引保护需求，中断即重跑 |

## 10. Non-goals

- ✗ 表格检索策略（table-only retrieval / table-aware rerank）→ 二期
- ✗ Evidence Gate（KEEP DISABLED / DEFER）
- ✗ Evaluator v1 任何改动
- ✗ ANN 索引（维度迁移因此无需重建索引）
- ✗ Alembic 收敛 / prompt / reranker / RRF 权重调整
- ✗ 大表 LLM 摘要（→ Baseline-5 独立实验，本期不做）

# Day 2 上午 Done + 反思 (2026-08-23)

> 接 [docs/plans/2026-08-22-rag-decomposition.md](docs/plans/2026-08-22-rag-decomposition.md) §九 Day 2 上午。

## 本次完成（2026-08-21）

### 完成步骤

按 plan §九 Day 2 上午 7 步执行，**代码 + 测试完成，但 live verification 暴露设计问题**：

1. ✅ `app/ingestion/embedding_text.py::build_embedding_text()` 纯函数（10 tests）
2. ✅ `app/store/db.py` 加 6 列：`year` / `page_start` / `page_end` / `embedding_version` / `table_title` / `figure_title` + 2 索引
3. ✅ `app/ingestion/indexer.py:284` 改用 `build_embedding_text(c, doc)` 喂 embedding + 写入 `embedding_text` 字段 + `embedding_version=2`
4. ✅ `app/ingestion/metadata.py` 删 `EmbeddingTextEnhancer` 类（无 live code 用，119 行死代码清掉）
5. ✅ `app/store/pgvector_store.py` 三个 SQL 通道（search/bm25/question）加 `AND embedding_version = :ev` 过滤
6. ✅ `tools/reembed_v2.py` 批量重 embed（1381/1381 → v2，124 秒，0 失败）
7. ✅ Smoke 跑通（retriever 工作）

### 修改文件

- `app/ingestion/embedding_text.py`（新增）— build_embedding_text 纯函数 + EMBEDDING_TEXT_VERSION=2
- `app/store/db.py` — Chunk 模型加 6 列 + init_db 加 6 ALTER TABLE + 2 CREATE INDEX
- `app/config.py` — 加 `current_embedding_version: int = 1`（默认）
- `app/ingestion/indexer.py` — `_embed_inputs = [build_embedding_text(c, doc) for c in new_chunks]` + chunks_data 加 embedding_text + embedding_version
- `app/store/pgvector_store.py` — replace_chunks 写入新字段 + 三个 SQL 加 embedding_version filter
- `app/ingestion/metadata.py` — 删 EmbeddingTextEnhancer（119 行）
- `tools/reembed_v2.py`（新增）— 批量回填 + 重 embed，支持 --limit/--dry-run

### 新增测试

- `tests/unit/test_embedding_text.py` — 10 tests（覆盖空 chunk / 字段顺序 / 跳过空字段 / doc_filename vs chunk_title / 不含 summary / 不含 questions / deterministic）

### 验证结果

**Unit tests**：373 → 383 passed（+10），2 pre-existing fail（test_chunker + test_retrieval_year_coverage 与本次无关）

**Live full 65 baseline（v1 vs v2）**：

| 指标 | v1 (Day 1 晚上) | **v2 (Day 2 上午)** | Δ |
|---|---|---|---|
| hit@5 | 0.984 | 0.969 | -1.5pp |
| hit@10 | **1.000** | **1.000** | 持平 |
| recall@5 | 0.940 | 0.911 | -2.9pp |
| recall@10 | 1.000 | **0.984** | **-1.6pp** |
| MRR | 0.876 | **0.824** | **-5.2pp** |

## ⚠️ 严重问题：v2 embedding 改造**实际退化指标**

### 现象

3 题部分召回下降（gold=[2023,2024,2025] 但 top-10 只召 2/3）：

| Q | 类别 | gold | retrieved (top-10) |
|---|---|---|---|
| Q17 | C-跨文档对比 | [2023, 2024, 2025] | 2024×5, 2023×5（**2025 全失**） |
| Q19 | C-跨文档对比 | [2023, 2024, 2025] | 2024×7, 2023×3（**2025 全失**） |
| Q51 | H-错误前提纠偏 | [2023, 2024, 2025] | 2023×8, 2024×2（**2025 全失**） |

### 原因（推测）

v2 embedding 输入：
```
文档：三一重工_2025年年度报告.pdf
章节：2025年 >  > 三一重工股份有限公司 2025 年年度报告 > 第四节公司治理、环境和社会 > (一) ...
正文：xxx
```

- "文档：xxx" + "章节：xxx" 前缀占大量 token
- embedding 模型没训练过识别"文档："这种 prefix 标签
- 实际正文（营收数字、表格数据）被稀释到 embedding 的"细节层"
- query 提到"2025"时，"文档：三一重工_2025年年度报告.pdf" 部分可能匹配，但**正文相似度被弱化**

⚠️ **2025 doc 普遍消失**说明这不只是 top-1 排序问题——是 2025 的所有 chunks 在 hybrid 检索中整体排名降低。

### 不可逆

**v1 embedding 已被覆盖**——DB 里 v1 chunks 数 = 0，所有 1381 chunks 都是 v2。回滚到 v1 会让检索返回空结果。

恢复路径只有：
1. 把 v2 chunks 标回 `embedding_version=1`（hack，版本号撒谎）
2. 重新跑 `reembed_v2.py` 用 `c.text`（不是 build_embedding_text）作为输入，标 v1
3. 接受 v2 现状，调 generation 层 / build_embedding_text 设计

## 当前状态

| 项 | 状态 |
|---|---|
| 代码（7/7 步） | ✅ 完成 |
| 10 新 unit test | ✅ 全绿 |
| 383 unit tests 通过 | ✅ |
| 6 DB 列 + 2 索引 | ✅ 已加（生产 DB） |
| 1381 chunks 已重 embed 到 v2 | ✅ |
| baseline 退化 | ⚠️ recall@10 -1.6pp，MRR -5.2pp |
| v1 数据丢失 | ⚠️ 不可逆 |

## 下一步（建议）

### 短期（紧急 — 把 v2 修正回合理水平）

1. **重 build v1 embedding**（用 `c.text` 而不是 `build_embedding_text`），标 `embedding_version=1`
2. 切回 `current_embedding_version=1`
3. 验证 baseline 回到 0.876 MRR
4. **延迟 build_embedding_text 的激活**——重新设计 prefix 格式或权重

### 中期（重新设计 build_embedding_text）

可能的方向：
- **降权 prefix**：把 prefix 从 chunk.text 前移改为 embedding 平均池化（prefix 单独 embedding 再加权合并）
- **缩短 prefix**：只保留最有信号的（去 section_path，因为太长重复）
- **不动 embedding，只改 SQL filter**：用 `c.chunk.year` 而不是把 year 放进 embedding_text
- **实验不同 prefix 模板**：A/B 测试 `[DOC] xxx [/DOC] xxx [CONTENT] xxx`

### 长期

5. **Day 2 下午 — Evidence** 接入（已有 314 行活代码，pipeline.py:300 接入）
6. **Day 2 晚上 — 8 组 ablation** ——重新校准基线 + 找出真正有效的策略
7. **plan §十 验收 — baseline 65 题 ≥ 73.3%（answer-level）**——需要重跑 `eval/eval_sany.py`（带 LLM judge）

## 当前 backend 配置

- `current_embedding_version: int = 2`（已激活）
- 5 个检索层策略默认 **False**（cross_doc/section_boost/section_supplement/year_supplement/query_decomposition）
- cache 默认开（embedding + retrieval）
- 后端跑在 :8000，DB 中 v1=0/v2=1381

## 关键风险

1. **production 已用 v2 embedding**——任何新的 ingest 也会写 v2；如果用户报告检索质量下降，可能就是这个改造引起的
2. **plan §十 验收第 5 条**（baseline ≥ 73.3%）现在大概率过不了——v1 baseline 已废，v2 MRR -5.2pp
3. **不能简单 git revert**——v1 chunks 已经不存在了，DB 状态不可逆
4. **build_embedding_text 设计需要重新考虑**——不是简单禁用能解决的

## 关键文件改动一览

| 文件 | 状态 | 说明 |
|---|---|---|
| `app/ingestion/embedding_text.py` | 新增 | build_embedding_text() 纯函数 |
| `tools/reembed_v2.py` | 新增 | 批量 reembed 工具 |
| `tests/unit/test_embedding_text.py` | 新增 | 10 tests |
| `app/store/db.py` | 改 | Chunk 加 6 列 + init_db ALTER |
| `app/config.py` | 改 | current_embedding_version |
| `app/ingestion/indexer.py` | 改 | embed() 输入改 build_embedding_text + 写新字段 |
| `app/store/pgvector_store.py` | 改 | replace_chunks + 3 SQL 加 version filter |
| `app/ingestion/metadata.py` | 删 | EmbeddingTextEnhancer 119 行 |

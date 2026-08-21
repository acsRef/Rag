# RAG Ingestion + Retrieval 解耦重构方案

## Context

本次 context expansion 修复（pipeline.py:357-374）让 C 类从 62.5% → 70.8%（+8.3pp），但探出更深层问题：**每个优化改动都难以单独验证**，因为 Chunk 模型、embedding 输入、retrieval 流程、context 格式化全都耦合在一起。

**两个 explore agent 探查确认的现状**：

1. `embedding_text` column 在 `db.py:232` 已存在，但 `indexer.py:284` 调 `embed(c.text)` 永远不写它——**死字段**。
2. `app/core/evidence.py` 写完 314 行（`EvidenceTable`、`ConflictDetector`、`EvidenceOrganizer.format_for_prompt`）但 `pipeline.py:6` import 后**从不调用**——**死代码**。
3. `section_boost` / `_supplement_authoritative_sections` / `_supplement_missing_years` / `cross_doc_retriever` 在 `app/core/retrieval.py` 都**无条件执行**，没有 `*_enabled` 设置。
4. `year` 是从 `Document.filename` 解析（`indexer.py:_extract_year_from_filename`），不是 DB 字段。
5. 没有 `RetrievalFilter` dataclass：`document_ids` 散装参数穿过 SQL。
6. 没有 cache。
7. 没有 retrieval-only eval：所有 eval 走 `/api/v1/chat/stream`（`/api/v1/retrieve` endpoint 已存在但没人用）。
8. `EmbeddingTextEnhancer` (14B LLM) 在 `metadata.py:126-244` 写完但**从未被 indexer 调用**——只有 ad-hoc tools 用。
9. Pipeline 实际顺序（不是 canonical `Query → Filter → Retrieve → Rerank → Evidence → LLM`）：
   - history → PII → `_needs_decomposition` → `query_rewrite` → `intent_classify` → `retrieve` (asyncio.gather per sub_query) → dedup → `_truncate_with_doc_diversity` → year injection (post-hoc) → cross-doc synthesize → `prompt_builder.build_messages` → stream LLM

## 重构目标

1. 区分 `text` vs `embedding_text` vs `metadata`（**激活**死字段，不是新增）
2. ingestion 每步只负责一件事
3. retrieval 改成 `Query → Filter → Vector+BM25 → Rerank` 最简 baseline，所有"高级策略"做成可关闭 feature flag
4. year filtering **先于** retrieval 而不是依赖 embedding
5. 把 evidence 层从死代码变成可调用
6. eval 拆成 retrieval-only 和 generation 两套，**调 retrieval 时不调 LLM**
7. 加入 embedding/retrieval cache 加速迭代

**绝对不做**：重写整个项目。本次重构保留现有 80% 代码，只调整**数据流**和**模块边界**。

---

## 一、Chunk 数据结构重构

### 1.1 在 `app/store/db.py` 定义 Chunk 字段

**已有**（db.py:225-242）：`id, chunk_id, document_id, kb_id, text, embedding_text, embedding, title, summary, questions, section_path, search_text, content_hash, visibility, allowed_roles, created_at`

**关键发现**：`embedding_text` column **已存在但从未被写入**（indexer.py:284 调 `embed(c.text)`）。本次修复**激活**它，而不是新增 column。

**需要新增**：
- `year                INT NULL`      ← 新增，metadata filter 关键字段
- `page_start          INT NULL`      ← 新增
- `page_end            INT NULL`      ← 新增
- `embedding_version   INT DEFAULT 1` # ← 新增，embedding 改 schema 后用版本隔离
- `table_title         TEXT NULL`     ← 新增，标记 chunk 是否为表格
- `figure_title        TEXT NULL`     ← 新增

**action**：
- 在 `app/store/db.py` 的 Chunk 模型**追加**上述字段（记得 init_db 用 `ADD COLUMN IF NOT EXISTS`）
- 跑一次 `init_db()` 添加新列
- 注意：**不要删 `embedding_text` column**——保留并正确填充它

### 1.2 强化 `app/ingestion/metadata.py` 的元数据生成

- 文档级别：解析时提取 `year`、`document_title`、`source_type`
- chunk 级别：从 `section_path` 取最后一段作为 `section_title`；从正文识别表格/图标题
- 函数：`enrich_chunk_metadata(chunk, doc_meta) -> Chunk` 直接 in-place 写回

**action**：
- `app/ingestion/metadata.py` 新增 `enrich_chunk_metadata()` 函数
- 在 `app/ingestion/indexer.py:35 DocumentIndexer.index` 流程里调用一次

### 1.3 重建 `embedding_text` 拼装函数

新建 `app/ingestion/embedding_text.py`：

```python
EMBEDDING_TEXT_VERSION = 2  # 跟 db.embedding_version 对齐

def build_embedding_text(chunk: Chunk, doc: Document) -> str:
    parts = []
    if doc.title:
        parts.append(f"文档：{doc.title}")
    if chunk.section_path:
        parts.append(f"章节：{chunk.section_path}")
    if chunk.table_title:
        parts.append(f"表格：{chunk.table_title}")
    if chunk.figure_title:
        parts.append(f"图表：{chunk.figure_title}")
    parts.append(f"正文：{chunk.text}")
    return "\n".join(parts)
```

**action**：
- 新增 `app/ingestion/embedding_text.py`
- 在 `app/ingestion/indexer.py` 的 embedding 阶段调用 `build_embedding_text()` 写回 `chunk.embedding_text`，再用它调 `embed()`
- 删除 `app/ingestion/metadata.py` 里 `EmbeddingTextEnhancer` 调 LLM 的旧实现（已弃用）

---

## 二、RetrievalFilter 抽象

### 2.1 新建 `app/core/retrieval_filter.py`

```python
from dataclasses import dataclass, field

@dataclass
class RetrievalFilter:
    years: set[int] | None = None
    document_ids: set[str] | None = None
    section_names: set[str] | None = None
    source_types: set[str] | None = None
    kb_ids: set[str] | None = None
```

### 2.2 修改 `app/store/pgvector_store.py:340 hybrid_search()` 签名

```python
def hybrid_search(
    query: str,
    query_embedding: list[float],
    filters: RetrievalFilter | None = None,  # ← 新增
    top_k: int = 30,
) -> list[SearchHit]:
    ...
    # vector / bm25 SQL 都加 chunk_filters = filters
```

**action**：
- 在 `hybrid_search` 开头把 `RetrievalFilter` 翻译成 SQL `WHERE` 子句
- 字段映射：years → `chunk.year IN (...)`，document_ids → `chunk.document_id IN (...)`，kb_ids → `chunk.knowledge_base_id IN (...)`

### 2.3 在 `app/core/retrieval.py` 把 `_supplement_authoritative_sections` 等"散装过滤"统一替换

**action**：
- 现有 `app/core/retrieval.py` 里所有 `if year == ...` / `if doc_id in ...` 散装判断，**全部删掉**
- 替换为 `RetrievalFilter` 注入
- 保留 `_supplement_authoritative_sections` 逻辑（它做的是"补全权威章节"），但传入 `RetrievalFilter` 而不是 ad-hoc 字面量

---

## 三、Query Parser / Year Filter

### 3.1 新建 `app/core/query_parser.py`

```python
@dataclass
class ParsedQuery:
    raw: str
    intent_metric: str | None = None  # "营业收入"
    year: int | None = None
    document_id: str | None = None
    section_name: str | None = None
    filters: RetrievalFilter = field(default_factory=RetrievalFilter)
```

**实现方式**：
- 优先用规则（正则 `\d{4}` 提取年份，关键词表提取指标）
- 兜底用 LLM（简短的 JSON 输出），跟 intent 复用
- 函数 `parse_query(query: str) -> ParsedQuery`

### 3.2 在 `app/core/pipeline.py` 接入

```python
parsed = parse_query(req.query)
# 把 parsed.filters 透传到 retrieval.retrieve()
```

**action**：
- 新增 `app/core/query_parser.py`
- `app/core/pipeline.py` 在 `retrieve` 之前调 `parse_query()`
- 删除现有的"靠 embedding 相似度推断年份"的所有代码

---

## 四、策略可插拔（先关后开）

### 4.1 在 `app/config.py` 加策略开关

**已有**（config.py 已有）：
- `hybrid_search_enabled: bool = True` — vector vs hybrid
- `question_channel_enabled: bool = True` — 第三 RRF 通道
- `mmr_enabled: bool = True` — MMR
- `cross_doc_embedding_threshold: float = 0.7` — cross-doc doc-level cosine 阈值

**缺**（重点新增）：
- `cross_doc_enabled: bool = True` — 整体 master switch（当前无条件执行）
- `section_boost_enabled: bool = True` — 当前 `_boost_by_section_type` 无条件
- `section_supplement_enabled: bool = True` — `_supplement_authoritative_sections` 无条件
- `year_supplement_enabled: bool = True` — `_supplement_missing_years` 无条件
- `_needs_decomposition` 改为 `query_decomposition_enabled: bool = True`
- `evidence_gate_enabled: bool = False` — 新增
- `embedding_cache_enabled: bool = True` — 新增
- `retrieval_cache_enabled: bool = True` — 新增

### 4.2 整理 baseline

关闭所有高级策略后，pipeline 必须能跑出"干净" baseline：

```yaml
strategies:
  question_channel: false
  cross_doc: false
  mmr: false
  section_boost: false
  section_supplement: false
  year_supplement: false
  query_decomposition: false
  evidence_gate: false
```

**action**：
- `app/core/retrieval.py` 在每一处无条件调用（line 593, 782, 788, 617-642）的开头加 `if settings.xxx_enabled:` 一行 guard
- `app/core/pipeline.py` 在 `_needs_decomposition` 调用前加 `if settings.query_decomposition_enabled:`，否则降级为单查询路径
- 默认 baseline 配置：所有策略关，**评测先跑 baseline**

---

## 五、Evidence 层正式化

### 5.1 强化 `app/core/evidence.py` 类型

```python
@dataclass
class EvidenceResult:
    coverage: float                # 0~1
    temporal_consistent: bool
    conflicts: list[dict]
    sources: list[dict]
    coverage_by_year: dict[int, float]  # 新增：按年份看覆盖
```

### 5.2 接入 pipeline

```python
if settings.evidence_gate_enabled:
    evidence = build_evidence(candidates)
    if evidence.coverage < settings.evidence_min_coverage:
        return refusal_or_followup()
```

**action**：
- `app/core/evidence.py` 改成 `EvidenceResult` dataclass
- `app/core/pipeline.py` 加 `if evidence_gate_enabled:` 块
- `app/config.py` 加 `evidence_min_coverage: float = 0.7`

---

## 六、Embedding/Retrieval Cache

### 6.1 新建 `app/core/cache.py`

```python
class EmbeddingCache:
    def __init__(self, redis_url=None):  # 内存或 redis 二选一
        ...
    def get(text: str) -> list[float] | None: ...
    def set(text: str, vec: list[float]) -> None: ...

class RetrievalCache:
    def key_for(query, filters, top_k, config_version) -> str: ...
    def get(key) -> list[SearchHit] | None: ...
    def set(key, hits) -> None: ...
```

### 6.2 接入

- `app/llm/embedding.py` 调 `embed()` 前先问 `EmbeddingCache`
- `app/core/retrieval.py` 调 `retrieve()` 前先问 `RetrievalCache`，key 包含 `filters + top_k + config_version`

**action**：
- 新增 `app/core/cache.py`
- `app/llm/embedding.py` 集成 `EmbeddingCache`
- `app/core/retrieval.py` 集成 `RetrievalCache`
- 配置：`app/config.py` 加 `embedding_cache_enabled: bool = True`、`retrieval_cache_enabled: bool = True`

---

## 七、Eval 拆分

### 7.1 新建 `eval/retrieval_eval.py`

```python
# 只跑：Query → Filters → Retriever → TopK
# 指标：Recall@5, Recall@10, MRR, Hit Rate
# 不调 LLM
```

### 7.2 强化 `eval/generation_eval.py`（原 eval/）

- 复用 `eval/eval_single.py` 现有逻辑
- **保留** LLM judge 路径

### 7.3 三档测试配置

**已有**：`eval/sany_annual_reports/rag_testset.json` 65 题分 10 类（A/B/C/D/E/F/G/H/I/J），3 难度（简单/中等/困难）。

**新增** `eval/sany_annual_reports/tiers.json`：

```json
{
  "smoke": ["Q17", "Q18", "Q20", "Q22", "Q27", "Q02", "Q03", "Q11", "Q44", "Q57"],
  "regression": [20 题，覆盖每类至少 2 题],
  "full": [...all 65]
}
```

### 7.4 evalset 强化

**已有**：每题有 `id/类别/题型/难度/问题/参考答案/答案依据/考察的RAG易错点/常见错误答案`。

**新增**：

```json
{
  "gold_documents": ["doc_2023", "doc_2024"],
  "gold_chunks": ["f4b0be8aaa644522_529c3649c7", ...],
  "expected_answer": "..."
}
```

**action**：
- 新增 `eval/retrieval_eval.py`：调 `/api/v1/retrieve`（已有，未用），不调 LLM
- 新增 `eval/metrics.py`（Recall/MRR/Hit Rate）
- 强化 `eval/sany_annual_reports/rag_testset.json` 加 tier 字段 + gold_documents
- `eval/eval_single.py` 加 `--tier smoke|regression|full` 参数
- `eval/eval_results.json` 已有 `judge_score / judge_reason / sources_count`，扩展加 `retrieval_metrics` 字段

---

## 八、按文件级修改清单

| 文件 | 改动 | 优先级 |
| --- | --- | --- |
| `app/store/db.py` | 加 `year, page_start, page_end, embedding_version, table_title, figure_title` 列（228 附近） | P0 |
| `app/ingestion/embedding_text.py` | 新建 `build_embedding_text()`，纯函数，无 LLM 依赖 | P0 |
| `app/ingestion/indexer.py:284` | 改 `embed_with_fallback([c.text for c in new_chunks])` → `embed_with_fallback([build_embedding_text(c, doc) for c in new_chunks])`；写入 `chunk.embedding_text` | P0 |
| `app/ingestion/indexer.py:333` | 替换 chunks_data 写入"text"为批量写入 "text + embedding_text + year" | P0 |
| `app/ingestion/metadata.py` | 删 `EmbeddingTextEnhancer`（126-244）+ `embedding_text_enhancer` singleton(244)；改 `chunk_metadata_generator` 调一次普通 LLM（不要在 embedding_text 里调 LLM） | P0 |
| `app/core/retrieval_filter.py` | 新建 `RetrievalFilter` dataclass | P0 |
| `app/core/query_parser.py` | 新建 `parse_query()` + `ParsedQuery` dataclass | P0 |
| `app/core/pipeline.py:168-403` | 接入 `parse_query()`；所有策略加 `if settings.xxx_enabled:` guard | P0 |
| `app/store/pgvector_store.py:398 hybrid_search` | 接收 `filters: RetrievalFilter`，翻译成 SQL `WHERE` | P0 |
| `app/core/retrieval.py:593 (section_supplement)` | 开头加 `if not settings.section_supplement_enabled: return candidates` | P0 |
| `app/core/retrieval.py:782 (year_supplement)` | 同上 `if not settings.year_supplement_enabled: return candidates` | P0 |
| `app/core/retrieval.py:788 (section_boost)` | 同上 `if not settings.section_boost_enabled: return candidates` | P0 |
| `app/core/retrieval.py:617-642 (cross_doc)` | 加 `if not settings.cross_doc_enabled:` guard | P0 |
| `app/core/evidence.py:563` | 改 `EvidenceOrganizer` 返回 `EvidenceResult` dataclass（已部分实现 `EvidenceTable`，加 `overall_coverage` → `coverage` 别名 + `temporal_consistent` + `conflicts`） | P1 |
| `app/core/pipeline.py:403` | 接入 `evidence_organizer.organize()`，加 `if settings.evidence_gate_enabled:` 块 | P1 |
| `app/core/cache.py` | 新建 `EmbeddingCache` + `RetrievalCache`（内存版即可，先不接 redis） | P1 |
| `app/llm/embedding.py` | 集成 `EmbeddingCache`（hash(text)→vec） | P1 |
| `app/core/retrieval.py` | 集成 `RetrievalCache`（hash(query + filters + config_version)→candidates） | P1 |
| `app/config.py` | 加 7 个策略开关 + `evidence_min_coverage`、`embedding_cache_enabled`、`retrieval_cache_enabled` | P1 |
| `eval/retrieval_eval.py` | 新建，调 `/api/v1/retrieve`（已存在未用），不调 LLM | P1 |
| `eval/metrics.py` | 新建 Recall@5/10/MRR/HitRate | P1 |
| `eval/sany_annual_reports/rag_testset.json` | 每题加 `gold_documents`, `gold_chunks` | P1 |
| `eval/sany_annual_reports/tiers.json` | 新建 smoke/regression/full tier 配置 | P1 |
| `eval/eval_single.py` | 加 `--tier smoke|regression|full` 参数 | P2 |
| `tools/rollback_embeddings.py` | 改用 `build_embedding_text()` | P2 |
| `tools/reembed_with_embedding_text.py` | 同上 | P2 |
| `tests/unit/test_evidence.py` | 加 `EvidenceResult` 类型测试 | P2 |
| `tests/unit/test_query_parser.py` | 新建覆盖 `_extract_year` + `_extract_metric` | P2 |
| `tests/unit/test_chunk_metadata.py` | 新建覆盖 `build_embedding_text()` | P2 |

---

## 九、实施顺序（2 天）

### Day 1 上午：Eval & Cache
1. 拆 testset 为 smoke / regression / full：`eval/sany_annual_reports/tiers.json`
2. 新增 `eval/retrieval_eval.py`：调 `/api/v1/retrieve`（已存在），不调 LLM
3. 新增 `eval/metrics.py`（Recall@5/10/MRR/Hit Rate）
4. 新增 `app/core/cache.py` + `EmbeddingCache`（内存版，hash(text)→vec）
5. 集成 `EmbeddingCache` 到 `app/llm/embedding.py`
6. 跑 baseline smoke（10 题）验证 retriever 没坏

**验证**：`D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q` 全部通过

### Day 1 下午：策略开关 + RetrievalFilter 接 baseline
1. `app/config.py` 加 7 个策略开关 + cache 开关 + `evidence_min_coverage`
2. `app/core/retrieval.py` 在 4 处无条件调用加 `if settings.xxx_enabled:` guard（line 593, 782, 788, 617-642）
3. `app/core/pipeline.py` 在 `_needs_decomposition` 前加 `if settings.query_decomposition_enabled:`
4. 新增 `app/core/retrieval_filter.py`（dataclass）
5. `app/store/pgvector_store.py:398 hybrid_search` 接收 `filters`，翻译成 SQL
6. `app/core/retrieval.py` 把内部的 `document_ids` 参数和散装过滤替换成 `RetrievalFilter`
7. 跑 baseline smoke（10 题关闭所有策略），跟 73.3% baseline 对比

**验收**：关闭所有策略后分数 ≤ 73.3%（如果高了，说明这些策略真是无效噪音；记录）

### Day 1 晚上：Query Parser + Year Filter
1. 新增 `app/core/query_parser.py`（正则提取年份 + 关键词提取指标）
2. `app/core/pipeline.py` 接入 `parse_query()`，把 `ParsedQuery.filters` 传给 `retrieval.retrieve()`
3. 跑 regression 测试集（20 题），看 temporal 类（E-时序与追溯调整）

**验收**：E 类分数 ≥ 70%（当前 61.1%）

### Day 2 上午：Embedding 改造
1. 新增 `app/ingestion/embedding_text.py`（`build_embedding_text()`，纯函数）
2. `app/ingestion/indexer.py:284` 改用 `build_embedding_text()` 喂 embedding
3. `app/ingestion/indexer.py:333` 写入 `text + embedding_text`
4. `app/store/db.py` 加 `embedding_version` 列
5. 在 `app/store/pgvector_store.py:398 hybrid_search` 加 `AND embedding_version = :current_version` 过滤
6. **写一个 tools/reembed_v2.py**：批量把 `chunk.embedding_text` 当输入重新 embed，写入 `embedding` + `embedding_version=2`
7. 跑 baseline smoke，看是否改善

**注意**：现有 indexer 不写 `embedding_text`，所以 `reembed_v2.py` 还要先**回填** `embedding_text`（用 `build_embedding_text(c, doc)` 对所有 chunk）

### Day 2 下午：Evidence
1. `app/core/evidence.py` 暴露 `EvidenceResult` dataclass
2. `app/core/pipeline.py:300` 接入 `evidence_organizer.organize(query, sub_question_chunks, query_type)`（此时 `sub_question_chunks` 已填充）
3. 加 `if settings.evidence_gate_enabled: ... return refusal_or_followup()` 块
4. 跑 smoke 验证

**验收**：smoke 10 题不恶化

### Day 2 晚上：Ablation
跑下面 8 组实验，写一份 ablation 报告 `docs/plans/2026-08-23-ablation-report.md`：

| Pipeline | Recall@10 | Answer Acc | Latency |
| --- | --- | --- | --- |
| Baseline (no flags) | | | |
| + Year Filter (query_parser) | | | |
| + Section Embedding (build_embedding_text) | | | |
| + Rerank | | | |
| + Section Boost | | | |
| + MMR | | | |
| + Cross-doc | | | |
| + Question Channel | | | |
| + Evidence Gate | | | |

每组跑 regression 20 题，记录 Recall@5/10/MRR/Hit Rate + Answer Accuracy

---

## 十、验收标准

1. **所有 unit test 通过**（267 个）
2. **smoke 10 题跑完 < 30 秒**（cache 生效）
3. **retrieval-only eval 跑 65 题 < 1 分钟**（不调 LLM）
4. **baseline 全跑 65 题分数 ≥ 73.3%**（当前修复后分数）
5. **关闭所有策略后基线分数 ≤ 73.3%**（回归生效，证明策略真的在做贡献）
6. **ablation 报告生成**（8 组对照实验）

---

## 风险与注意事项

1. **`embedding_version` 字段必须加**：如果不加，老 embedding 和新 embedding 混用会乱
2. **`text` 永远不要变**：只读，LLM 看到的"正文"永远是 `text`；`embedding_text` 只用于 embedding
3. **year 字段从文档元数据提取，不要让 LLM 猜**：LLM 提取的 year 90% 会有噪声——直接用 `Document.filename` 解析（`_extract_year_from_filename` 已存在）
4. **关闭所有策略后，basline 必须能跑**：万一打开策略把 baseline 搞坏了，至少能跑出 baseline
5. **不要重写现有 embedding 全部 chunk**：用 `embedding_version=2` 隔离，新 embedding 跑完手动 swap
6. **`EmbeddingTextEnhancer` 必须删**：它是 14B LLM 增强输入会拖慢 ingest 1000 倍，而且从来没人用——`build_embedding_text()` 是纯函数更快更可控
7. **`EvidenceOrganizer` 必须从死代码变活代码**：之前 314 行白写——本重构把它当成"已知能力"接入，**不要重新设计它的 dataclass，只暴露 `EvidenceResult` 包装**
8. **`/api/v1/retrieve` 已存在**：直接用，不要新建 retrieval endpoint

---

## 下一步

等用户确认 plan，写到 `docs/plans/2026-08-22-rag-decomposition.md` 作为正式项目计划文档即可开始 Day 1 上午。

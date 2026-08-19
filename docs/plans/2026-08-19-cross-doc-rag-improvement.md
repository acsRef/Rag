# 跨文档 RAG 改进方案

> 日期：2026-08-19
> 目标：将 RAG 从"一次检索、直接生成"升级为"证据组织的推理流程"
> 参考：跨文档 RAG 方法论文章（问题分解→分阶段检索→证据重排→冲突消解→结构化生成）

---

## 现状分析

### 当前数据流
```
User Query
  → _needs_decomposition()         # 正则判断是否跨文档
  → rewrite.rewrite()              # 拆子问题 + sub_dependencies（已实现但未使用）
  → For each sub_question:
      → intent.classify()          # KB 路由
      → retrieval.retrieve()       # embed → hybrid_search → cross-doc → rerank → MMR
  → Merge all chunks (丢失子问题来源!)
  → Dedup + sort by score
  → _truncate_with_doc_diversity() # 每文档至少 1 chunk
  → Context expansion (±N neighbors)
  → cross_doc_synthesizer          # 按文档分组标注
  → prompt_builder.build_messages()# 直接拼 prompt
  → LLM streaming
```

### 核心问题
1. **子问题来源丢失**：各子查询的 chunks 合并后无法区分"这个 chunk 回答了哪个子问题"
2. **一次性检索**：chunk 级向量检索，不理解"该看哪个文档的哪个 section"
3. **排序只看相关性**：不对比类/汇总类问题做差异化排序
4. **无冲突检测**：不同文档的同一指标数值不同不会标记
5. **sub_dependencies 未使用**：rewrite 已产出依赖关系但 pipeline 完全忽略

---

## 改进总览

| 阶段 | 改动 | 工作量 | 依赖 |
|------|------|--------|------|
| P0 | 证据整理层 | 半天 | 无 |
| P1 | 分阶段检索 | 1天 | 无（但建议在 P0 之后） |
| P2 | 证据重排 | 半天 | P0 |
| P3 | 冲突消解 | 1天 | P0 + P1 |

---

## P0：证据整理层（Evidence Organizer）

### 目标
在 retrieval → prompt 之间加一层证据组织，让 LLM 看到结构化的证据表而非散装 chunks。

### 核心改动

#### 1. 保留子问题来源（pipeline.py）

当前 `_retrieve_one()` 返回 `list[RetrievedChunk]` 但合并时丢失来源。改为：

```python
# pipeline.py: 修改 _retrieve_one 返回带标记的结果
async def _retrieve_one(sub_q: str) -> tuple[str, list[RetrievedChunk]]:
    ...
    return sub_q, chunks

# 合并时保留来源映射
sub_question_chunks: dict[str, list[RetrievedChunk]] = {}
for sub_q, chunks in results_list:
    sub_question_chunks[sub_q] = chunks
```

#### 2. 新增 `app/core/evidence.py`

```python
"""Evidence organization layer — 检索结果 → 结构化证据表。

职责：
1. 按子问题归类 chunks
2. 标注覆盖度（哪些子问题有证据、哪些缺失）
3. 去重（同一事实出现在多个 chunk）
4. 生成 LLM 可读的证据表格式
"""

class EvidenceSlot:
    """一个证据槽位 = 一个子问题 + 支撑它的 chunks"""
    sub_question: str
    chunks: list[RetrievedChunk]
    covered: bool           # 是否有至少一个 chunk
    doc_ids: set[str]       # 涉及哪些文档

class EvidenceTable:
    """完整的证据表"""
    slots: list[EvidenceSlot]
    query_type: str         # "comparison" | "summary" | "single"
    overall_coverage: float # 0-1，有证据的子问题比例

class EvidenceOrganizer:
    def organize(
        self,
        sub_question_chunks: dict[str, list[RetrievedChunk]],
        query_type: str | None = None,  # 来自 rewrite.complexity 或 intent
    ) -> EvidenceTable:
        """将子问题→chunks 映射整理为结构化证据表"""
        ...

    def format_for_prompt(self, table: EvidenceTable) -> str:
        """将证据表格式化为 LLM 可读的文本"""
        ...
```

#### 3. 证据表 prompt 格式

对比类问题：
```
## 证据表

### 子问题 1：2023年营收
- [Source 1] 三一重工2023年年报 · 第二节 · 主要会计数据
  "营业收入为732.22亿元..."
- [Source 4] 三一重工2024年年报 · 第二节（对比列）
  "2023年度营业收入740.19亿元（调整后）..."

### 子问题 2：2024年营收
- [Source 2] 三一重工2024年年报 · 第二节
  "营业收入为778.15亿元..."

### 子问题 3：2025年营收
- [Source 3] 三一重工2025年年报 · 第二节
  "营业收入为812.50亿元..."

### 覆盖度
✅ 3/3 子问题均有证据支撑
⚠️ 子问题1存在两个不同数值（来自不同文档），请注意区分
```

汇总类问题：
```
## 证据表

### 主题：公司主要子公司信息
- [Source 1] 2023年年报 · 公司治理
  "主要子公司包括A、B、C..."
- [Source 2] 2024年年报 · 公司治理
  "主要子公司包括A、B、C、D..."（新增D）

### 覆盖度
✅ 信息充分
```

#### 4. Pipeline 集成

```python
# pipeline.py: 在 unique_chunks → prompt_builder 之间插入
from app.core.evidence import evidence_organizer

# 构建子问题→chunks 映射（从 all_chunks 回溯）
sub_question_chunks = {}
for sub_q, chunks in retrieve_results:  # 保留原始分组
    sub_question_chunks[sub_q] = chunks

evidence_table = evidence_organizer.organize(
    sub_question_chunks,
    query_type=rewrite_result.complexity,  # or intent_type
)

# 传给 prompt_builder
messages = prompt_builder.build_messages(
    query=req.query,
    ...
    evidence_table=evidence_table,  # 新增参数
)
```

#### 5. Prompt Builder 改动

```python
# prompt.py: build_messages 增加 evidence_table 参数
def build_messages(
    self,
    query, history, summary,
    retrieved_chunks,
    complexity="complex",
    evidence_table=None,  # 新增
) -> list[dict]:
    if evidence_table:
        context_str = evidence_organizer.format_for_prompt(evidence_table)
        # 使用证据表模板（带覆盖度提示）
    else:
        context_str = self._format_chunks(retrieved_chunks)
        # 降级为原有格式
    ...
```

### 文件改动清单

| 文件 | 改动 |
|------|------|
| `app/core/evidence.py` | **新建**，EvidenceOrganizer |
| `app/core/pipeline.py` | 保留子问题来源映射，插入 evidence organizer |
| `app/core/prompt.py` | 增加 evidence_table 参数和对应模板 |
| `app/models/schemas.py` | 增加 EvidenceTable 数据模型（可选） |

### 验证方法
- 单元测试：给定子问题→chunks 映射，验证证据表结构正确
- 集成测试：对比类问题，检查 prompt 中是否包含结构化证据表
- 评测：跑 `eval_sany.py`，对比 C 类得分变化

---

## P1：分阶段检索（Two-Stage Retrieval）

### 目标
先确定相关文档，再在文档内精搜。解决"检索到错误 section"问题。

### 核心思路

当前：
```
query → chunk-level vector search → 可能拿到错 section
```

改为：
```
query → document-level pre-retrieval → top relevant docs
     → chunk-level search WITHIN target docs → 精确 section
```

### 设计

#### 1. 文档级预检索

利用已有的 `doc_embeddings` 表（`doc_relation.py` 在 ingest 时已计算）：

```python
# retrieval.py: 新增文档级预检索
async def _pre_retrieve_documents(
    query_emb: list[float],
    kb_ids: list[str],
    top_k_docs: int = 5,
) -> list[str]:
    """返回最相关的 document_id 列表"""
    # 用 doc_embeddings 表做 cosine similarity
    # 已有 pgvector_store.get_doc_embeddings_bulk()
    ...
```

#### 2. 文档约束的 chunk 检索

在 `hybrid_search` 增加 `document_ids` 过滤参数：

```python
# pgvector_store.py: hybrid_search 增加文档过滤
def hybrid_search(
    kb_ids, embedding, query,
    top_k, fetch_k, rrf_k,
    document_ids=None,  # 新增：限定搜索范围的文档 ID
    ...
) -> list[dict]:
    if document_ids:
        # SQL WHERE clause: chunk.document_id IN (:document_ids)
        ...
```

#### 3. 两阶段检索流程

```python
# retrieval.py: 两阶段检索
async def retrieve(self, query, intent, ...):
    query_emb = await embed_query(query)

    if is_cross_doc_query:  # 来自 _needs_decomposition 或 rewrite
        # Stage 1: 文档级预检索
        doc_ids = await _pre_retrieve_documents(query_emb, kb_ids, top_k_docs=5)

        # Stage 2: 文档内精搜
        results = await _collect_results_with_doc_filter(
            kb_ids, query_emb, query,
            document_ids=doc_ids,  # 限定范围
            top_k=top_k,
        )
    else:
        # 单文档/简单查询：走原有路径
        results = await _collect_results(...)

    # 后续 rerank → MMR 不变
    ...
```

#### 4. 判断"是否跨文档查询"

复用 `_needs_decomposition()` 的结果，或更精确地：
- `rewrite_result.complexity == "complex"` 且 `len(sub_questions) > 1`
- 或 `intent_type` 包含 "comparison" / "multi_hop"

### 文件改动清单

| 文件 | 改动 |
|------|------|
| `app/core/retrieval.py` | 新增 `_pre_retrieve_documents()`，两阶段流程 |
| `app/store/pgvector_store.py` | `hybrid_search` 增加 `document_ids` 过滤 |
| `app/core/pipeline.py` | 传递 cross-doc 标记给 retrieval |

### 验证方法
- 单元测试：mock doc_embeddings，验证文档级排序正确
- 集成测试：对比类问题，验证 chunk 来自正确 section
- 评测：Q17（2023-2025营收），验证每年数据来自对应年报的正确 section

### 风险
- `doc_embeddings` 表可能未完全覆盖所有文档（需要 ingest 时计算）
- 文档级预检索可能遗漏相关文档（需要设合理的 top_k_docs）
- 两阶段检索增加延迟（需要 benchmark）

---

## P2：证据重排（Evidence-Aware Reranking）

### 目标
根据问题类型差异化排序：对比类保留成对证据，汇总类保留覆盖率高的。

### 依赖
P0（证据整理层的子问题归类）

### 设计

#### 1. 问题类型感知

从 `rewrite_result` 或 `intent` 获取问题类型：
- `comparison`：需要 A vs B 的成对证据
- `summary`：需要多来源覆盖
- `single`：单点事实，保持现有排序

#### 2. 对比类证据重排

```python
def _rerank_for_comparison(chunks: list[RetrievedChunk], sub_q_map: dict) -> list:
    """对比类：确保每对比较对象都有证据"""
    # 按文档/年份分组
    by_doc = group_by_document(chunks)

    # 检查每个比较维度是否都有覆盖
    # 如果某个维度缺失，提升该维度的 chunk 分数（从 cross-doc 或 context expansion 补充）
    ...
```

#### 3. 汇总类证据重排

```python
def _rerank_for_summary(chunks: list[RetrievedChunk], sub_q_map: dict) -> list:
    """汇总类：优先保留覆盖更多子问题的文档"""
    # 计算每个文档的 coverage_score = 它支撑了多少个子问题
    doc_coverage = {}
    for sub_q, chunks in sub_q_map.items():
        for c in chunks:
            doc_coverage[c.document_id] = doc_coverage.get(c.document_id, 0) + 1

    # 按 coverage_score 降序，同时保留每文档至少 1 chunk
    ...
```

#### 4. 集成位置

在 P0 的 `EvidenceOrganizer.organize()` 之后、`format_for_prompt()` 之前：

```python
evidence_table = evidence_organizer.organize(sub_question_chunks)
evidence_table = evidence_organizer.rerank_by_question_type(evidence_table)
prompt_str = evidence_organizer.format_for_prompt(evidence_table)
```

### 文件改动清单

| 文件 | 改动 |
|------|------|
| `app/core/evidence.py` | 增加 `rerank_by_question_type()` 方法 |

### 验证方法
- 单元测试：给定固定输入，验证对比类输出成对、汇总类输出覆盖率高
- 评测：对比 C 类和 B 类（汇总）得分变化

---

## P3：冲突消解（Conflict Resolution）

### 目标
检测同一指标在不同文档中的数值差异，标记冲突并按权威性消解。

### 依赖
P0（证据整理层）+ P1（分阶段检索的 section 信息）

### 设计

#### 1. 冲突检测

```python
class ConflictDetector:
    """检测同一指标在不同来源中的数值差异"""

    def detect(self, evidence_table: EvidenceTable) -> list[Conflict]:
        """扫描证据表，找出潜在冲突"""
        conflicts = []
        for slot in evidence_table.slots:
            # 提取数值（正则匹配数字+单位）
            values_by_source = self._extract_values(slot)
            # 检查同一指标是否有不同数值
            if self._has_conflict(values_by_source):
                conflicts.append(Conflict(
                    metric=...,
                    values=values_by_source,
                    severity="high" | "medium" | "low",
                ))
        return conflicts
```

#### 2. 冲突消解规则

```python
CONFLICT_RESOLUTION_RULES = [
    # 规则 1：时间权威性 — 问 X 年的数据用 X 年年报
    {"condition": "year_mismatch", "resolution": "prefer_matching_year"},
    # 规则 2：版本权威性 — 默认用最新
    {"condition": "version_conflict", "resolution": "prefer_latest"},
    # 规则 3：章节权威性 — 财务数据用财务报告节
    {"condition": "section_conflict", "resolution": "prefer_primary_section"},
]
```

#### 3. 冲突标注

在证据表中显式标记冲突：

```
### 子问题 1：2023年营收
- [Source 1] 2023年年报 · 第二节
  "营业收入为732.22亿元"  ← **原始披露**
- [Source 4] 2024年年报 · 对比列
  "2023年度营业收入740.19亿元"  ← **重述调整后**

⚠️ 冲突提示：2023年营收在两个来源中数值不同（732.22亿 vs 740.19亿）。
原因：2024年年报对2023年数据进行了会计调整重述。
建议：如用户问"2023年营收"，优先使用2023年年报的原始数据（732.22亿）。
```

#### 4. 集成

```python
# evidence.py
class EvidenceOrganizer:
    def organize(self, sub_question_chunks, query_type=None):
        table = self._build_table(sub_question_chunks)
        conflicts = self._conflict_detector.detect(table)
        table.conflicts = conflicts
        table = self._resolve_conflicts(table)  # 按规则标注优先级
        return table
```

### 文件改动清单

| 文件 | 改动 |
|------|------|
| `app/core/evidence.py` | 增加 ConflictDetector + 消解规则 |
| `app/core/prompt.py` | 冲突提示模板 |

### 验证方法
- 单元测试：给定已知冲突的数据，验证检测和消解正确
- 评测：对比有/无冲突消解的得分

### 风险
- 数值提取的正则可能不够健壮（需要处理各种单位格式）
- 冲突消解规则需要领域知识（不同场景规则不同）
- 过度标记冲突可能让回答变得冗长

---

## 实施路线图

```
Week 1:
  ├── Day 1-2: P0 证据整理层
  │     ├── evidence.py 实现
  │     ├── pipeline.py 集成
  │     └── prompt.py 模板
  │
  ├── Day 3-4: P1 分阶段检索
  │     ├── pgvector_store 文档过滤
  │     ├── retrieval.py 两阶段流程
  │     └── 性能测试
  │
Week 2:
  ├── Day 1: P2 证据重排
  │     ├── 对比类重排
  │     └── 汇总类重排
  │
  ├── Day 2-3: P3 冲突消解
  │     ├── 冲突检测
  │     ├── 消解规则
  │     └── prompt 集成
  │
  └── Day 4-5: 评测 + 调优
        ├── 跑 eval_sany.py 全量评测
        ├── 对比 C/H 类得分变化
        └── 针对 bad case 调优
```

---

## 评测验证

### 关键指标
| 类型 | 当前得分 | 目标 | 衡量方式 |
|------|---------|------|---------|
| C 类（跨文档对比） | 1.50/3 | 2.5+/3 | eval_sany.py 分类统计 |
| H 类（错误前提纠偏） | 0.00/3 | 2.0+/3 | 同上 |
| E 类（精确数据） | 1.83/3 | 2.5+/3 | 同上 |
| 整体 | 78.3% | 85%+ | 总分/满分 |

### Bad Cases 重点关注
- Q17: 2023-2025年营收（跨年份对比）
- Q18: 近三年海外收入（趋势对比）
- H 类所有题目（前提验证）

---

## 备注

- 所有改动保持向后兼容：如果 evidence organizer 失败，降级为原有散装 chunks 格式
- 不修改 db.py（SQLAlchemy 模型），所有新数据结构在应用层处理
- 新增 `app/core/evidence.py` 是纯逻辑模块，需要配套单元测试
- sub_dependencies 在 P0 阶段开始使用（控制证据表的呈现顺序）

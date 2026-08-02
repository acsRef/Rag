> 状态: 已完成（commits: 4d9dcef / 220cd99 / ed98bcd / 8e18b2c / 062d42b，分支 fix/cross-doc-overhaul）

# 跨文档检索改造（cross-doc-retrieval-overhaul）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让跨文档三通道检索真正生效并修掉检索链路的连环缺陷：三通道分数量纲统一、cross-doc 附加 chunk 不再被 RRF 量纲压死、channel 3 能独立发现文档、`get_chunks_by_document(s)` 补 `document_id` 与每文档上限、cross-doc 同步 DB 调用移出事件循环、rerank 截断不再静默丢候选、MMR 改真余弦且容忍 NULL 向量、引用编号与来源列表一致。

**Architecture:** 三层修：① 存储层补字段/上限/批量接口；② `doc_relation` 统一通道分数量纲（channel 1 除回 1000、channel 3 改批量并具备独立发现能力）；③ `retrieval`/`pipeline` 层：cross-doc 调用包 `to_thread`、附加 chunk 用"相对最强直连分的归一化映射"参与排序（而非 `min(score, max_rrf)` 压扁）、rerank 未返回候选追加回末尾、sources 事件移到跨文档合并之后。三个 xfail 锁定用例（`document_id` 缺失 / NULL embedding 崩溃 / 紧截断下 cross-doc 沉底）修复后转正。

**Tech Stack:** pgvector SQL（`ROW_NUMBER()` 分文档限流、批量 embedding 查询）、numpy（MMR 行归一化）、`asyncio.to_thread`、pytest（unit + integration，xfail 转正即验收）。

---

## Context

审查与测试基建期锁定的跨文档/检索缺陷（3 个已有 xfail 直接验收）：

1. **L1 分数量纲塌方（高危，xfail 锁定）**：`retrieval.py:175-177` 对 cross-doc 附加 chunk 做 `min(score, max_rrf)`——hybrid RRF 分量级 ~0.01，而通道分是 0–1（channel 1 甚至是 `int(cosine*1000)` = 0–1000！）→ 附加 chunk 全部压到 ≤0.01，候选截断后沉底，三通道机制在 hybrid 模式下失效。
2. **`get_chunks_by_document` 缺 `document_id`（中危，xfail 锁定）**：以其结果为 `initial_chunks` 时 `matched_doc_ids` 为空 → `cross_doc_retriever.retrieve` 直接返回 `[]`。
3. **事件循环阻塞（高危）**：`cross_doc_retriever.retrieve` 是 async 但内部全是同步 SQLAlchemy 调用，`retrieval.py:167` 直接 await → SSE handler 期间阻塞整个 worker；模块 docstring 还声称"不会阻塞"。
4. **channel 3 只能增强不能发现（中危）**：只遍历 `neighbor_scores` 已有 key，通道 1/2 无命中时语义兜底完全失效。
5. **channel 1/2/3 分数三量纲混用**：`cosine_scaled`（×1000 int）/ `match_ratio`（0–1）/ `cos_sim`（0–1）直接进同一个 max 聚合。
6. **`get_chunks_by_documents_bulk` 无上限无排序（中危）**：5 个 300 块的邻居文档 → 1500 行全量进 rerank。
7. **channel 3 N+1（中危）**：每个邻居文档单独查 `get_doc_embedding`。
8. **rerank 静默丢候选（中危）**：`results = [results[i] for i in reranked_ids]`——reranker 返回条目少于候选时，未返回的候选直接消失。
9. **MMR 内积冒充余弦 + NULL 崩溃（中危，NULL 部分 xfail 锁定）**：docstring 声称"上游已 L2 归一化"但全链路无人归一化；`embedding=None` 的 chunk 让 `np.array` shape 不一致直接崩。
10. **纯向量模式零向量兜底随机排序（中危）**：embedding 失败 + hybrid 关闭 → 全零向量余弦排序未定义。
11. **引用编号错位（中危）**：pipeline 先 yield sources 再做跨文档按文档合并去重，`[Source N]` 编号整体位移，答案引用与 UI 来源卡片对不上。

## Design

### 通道分数量纲统一（doc_relation.py）

- channel 1：`neighbor_scores[target] = max(..., rel["cosine_scaled"] / 1000.0)`——除回 0–1。
- channel 3 重写为**独立发现**：批量取语料内（`kb_ids` 范围）所有文档 embedding（新接口 `get_doc_embeddings_bulk`），排除已命中文档，`cos_sim >= cross_doc_embedding_threshold` 者入 `neighbor_scores`。语料规模 > 200 时只对 channel 1/2 候选求交（成本控制，与现状一致）；≤200 时全量评估——测试语料即走全量路径。
- 模块 docstring 更正：明确"内部为同步 DB 调用，必须由调用方包 `to_thread`"。

### 附加 chunk 的公平排序（retrieval.py）

`min(score, max_rrf)` 改为**归一化映射**：

```python
if extra:
    max_neighbor = max(c["score"] for c in extra)
    max_original = max((r["score"] for r in results), default=0.3)
    for c in extra:
        rel = c["score"] / max_neighbor if max_neighbor else 1.0
        # 排在最强直连分的 70%~100% 区间，保留邻居间次序，交给 rerank/MMR 定最终位置
        c["score"] = max_original * (0.7 + 0.3 * rel)
```

附加 chunk 稳定落在候选前列参与 rerank 与 MMR，不再被量纲压死，也不会无脑压过直连最佳结果。

### 存储层（pgvector_store.py）

- `get_chunks_by_document` 返回 dict 补 `"document_id": r.document_id`。
- `get_chunks_by_documents_bulk`：SQL 加 `ORDER BY document_id, id`；Python 侧每文档保留前 `_CHUNKS_PER_NEIGHBOR_DOC = 10` 条（邻居文档只需代表性上下文，最终条数由 rerank_top_k 收口）。
- 新增 `get_doc_embeddings_bulk(doc_ids) -> dict[str, list[float]]`，一次 `IN` 查询。

### 事件循环（retrieval.py）

`cross_doc_retriever.retrieve(...)` 调用包 `await asyncio.to_thread(...)`（async 函数体全是同步代码，to_thread 里直接 `asyncio.run`？不行——retrieve 是 async def。方案：在 doc_relation 提供同步孪生 `retrieve_sync`，async `retrieve` 保留为薄包装；retrieval.py 改调 `to_thread(cross_doc_retriever.retrieve_sync, ...)`）。

### rerank 不再丢候选（retrieval.py）

```python
reordered = [results[i] for i in reranked_ids]
returned = set(reranked_ids)
reordered += [r for i, r in enumerate(results) if i not in returned]  # 未返回的追加末尾
results = reordered
```

### MMR 硬化（mmr.py）

- 构建矩阵前：`_embedding_to_list(None)` → 记为缺失；矩阵按最大维度零填充。
- 每行 L2 归一化（零向量保持零）→ 内积即余弦，不再依赖上游承诺。全部缺失时退化为纯相关性排序。
- docstring 更正：归一化在本函数内完成。

### 零向量兜底（retrieval.py）

`embedding_degraded and not settings.hybrid_search_enabled` → 记 warning 并 `return []`（上层 pipeline 有全库兜底与无检索分支），不做随机排序。

### 引用编号一致（pipeline.py）

`sources` 事件的构建与 yield 移到跨文档合并去重块**之后**（`cross_doc` 事件之后），`_build_sources(unique_chunks)` 基于合并后的 chunk 列表 → `[Source N]` 与 UI 来源卡片、prompt 内编号三者一致。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| reranker 返回空 `[]`（失败吞掉） | 维持原排序（现状 `if reranked:` 守卫保留），全部候选存活 |
| reranker 返回部分索引 | 已返回的按新序在前，未返回的按原序追加 |
| 候选全部 embedding 为 NULL | MMR 退化为纯相关性 top-k |
| embedding 失败 + hybrid 关闭 | retrieval 直接返回 [] + warning（不做随机排序） |
| channel 3 语料 > 200 文档 | 只对 ch1/ch2 候选求交（成本上限） |
| 邻居文档 chunk 数 > 10 | 每文档取前 10（ORDER BY document_id, id 确定性） |
| initial_chunks 全部无 document_id | matched 为空 → 返回 []（现状保持；get_chunks_by_document 已补字段，正常路径不再触发） |

## Files to change

| 变更 | 路径 | 说明 |
|---|---|---|
| Modify | `app/store/pgvector_store.py` | 补 document_id；bulk 加排序+每文档上限；新增 `get_doc_embeddings_bulk` |
| Modify | `app/core/doc_relation.py` | channel 1 除 1000；channel 3 批量+独立发现；`retrieve_sync` 孪生；docstring 更正 |
| Modify | `app/core/retrieval.py` | to_thread 包装；归一化映射；rerank 追加；零向量守卫 |
| Modify | `app/core/mmr.py` | 零填充 + 行归一化；NULL 容忍 |
| Modify | `app/core/pipeline.py` | sources 事件后移到跨文档合并之后 |
| Modify | `tests/unit/test_mmr.py` | NULL xfail 转正；新增余弦归一化用例 |
| Modify | `tests/integration/test_cross_doc.py` | 2 个 xfail 转正；新增 channel 3 发现 / bulk 上限 / rerank 追加 3 例 |
| Modify | `docs/plans/README.md` | 登记；完成后转「已完成」 |

## Reused existing utilities

| 复用对象 | 路径 | 用途 |
|---|---|---|
| `_cosine_similarity` | `app/core/doc_relation.py` | channel 3 相似度计算不变 |
| `get_doc_embedding` 的查询模式 | `app/store/pgvector_store.py` | 批量版照单文档版的列/解码方式写 |
| `integration_db` / `ingest_docs` / `fake_llm_stack` | `tests/integration/conftest.py` | 语料与 fake embedding 直接复用 |
| 既有 xfail 用例 | tests/unit/test_mmr.py、tests/integration/test_cross_doc.py | 修复后去 marker 即验收，零新增断言成本 |

---

## Tasks

### Task 1: 存储层——document_id、bulk 上限、批量 embedding

**Files:** `app/store/pgvector_store.py`, `tests/integration/test_cross_doc.py`

- [ ] **Step 1: `get_chunks_by_document` 返回 dict 补字段**

在返回 dict 中 `"chunk_id": r.chunk_id,` 后加：

```python
                "document_id": r.document_id,
```

- [ ] **Step 2: `get_chunks_by_documents_bulk` 加排序与每文档上限**

SQL 末尾加 `ORDER BY document_id, id`；函数顶部定义 `_CHUNKS_PER_NEIGHBOR_DOC = 10`；Python 组装结果时按 document_id 计数，超过 10 条跳过：

```python
        per_doc_count: dict[str, int] = {}
        for r in rows:
            if per_doc_count.get(r.document_id, 0) >= _CHUNKS_PER_NEIGHBOR_DOC:
                continue
            per_doc_count[r.document_id] = per_doc_count.get(r.document_id, 0) + 1
            ...（原组装逻辑）
```

- [ ] **Step 3: 新增 `get_doc_embeddings_bulk`**

```python
def get_doc_embeddings_bulk(doc_ids: list[str]) -> dict[str, list]:
    """批量取文档级 embedding：{document_id: embedding}。"""
    if not doc_ids:
        return {}
    session = get_session()
    try:
        rows = (
            session.query(DocEmbedding.document_id, DocEmbedding.embedding)
            .filter(DocEmbedding.document_id.in_(doc_ids))
            .all()
        )
        return {r.document_id: r.embedding for r in rows if r.embedding is not None}
    finally:
        session.close()
```

（`DocEmbedding` 已在该文件导入；若未导入则补。）

- [ ] **Step 4: 转正 xfail——`tests/integration/test_cross_doc.py` 的 `test_get_chunks_by_document_includes_document_id` 删除 `@pytest.mark.xfail(...)` 装饰器**

- [ ] **Step 5: 新增 bulk 上限回归测试**

```python
def test_bulk_chunks_capped_per_doc(ingest_docs):
    """邻居文档 chunk 再多，bulk 每文档也不超过 10 条（防 rerank 被淹没）。"""
    from app.store.pgvector_store import get_chunks_by_documents_bulk
    bulk = get_chunks_by_documents_bulk(list(ingest_docs.values()), can_read_all=True)
    assert bulk
    for doc_id, chunks in bulk.items():
        assert len(chunks) <= 10
        assert all(c.get("document_id") == doc_id for c in chunks)
```

- [ ] **Step 6: 运行 + Commit**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_cross_doc.py tests/unit/test_mmr.py -q
git add app/store/pgvector_store.py tests/integration/test_cross_doc.py
git commit -m "fix(store): document_id in chunk dicts, per-doc cap + ordering in bulk, embeddings bulk API + plan: cross-doc-retrieval-overhaul"
```

---

### Task 2: MMR 硬化——零填充、行归一化、NULL 容忍

**Files:** `app/core/mmr.py`, `tests/unit/test_mmr.py`

- [ ] **Step 1: 写新用例 + 转正 xfail（tests/unit/test_mmr.py）**

删除 `test_null_embedding_does_not_crash` 的 xfail 装饰器，并新增：

```python
def test_mmr_normalizes_vectors_cosine_semantics():
    # 向量未归一化（范数 0.5）：旧实现用裸内积，多样性项几乎不起作用 → 选 b（同文档）；
    # 归一化后 a、b 方向相同（cos=1），c 应凭多样性胜出。
    cands = [
        _cand("a", 0.9, "doc1", [0.5, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [0.5, 0.0, 0.0]),
        _cand("c", 0.7, "doc2", [0.0, 0.5, 0.0]),
    ]
    out = mmr_select(cands, lambda_=0.5, top_k=2, max_per_doc=99, doc_penalty=0.0)
    assert [c["chunk_id"] for c in out] == ["a", "c"]


def test_all_null_embeddings_falls_back_to_relevance():
    cands = [
        {"chunk_id": "a", "score": 0.9, "document_id": "d1", "embedding": None},
        {"chunk_id": "b", "score": 0.5, "document_id": "d2", "embedding": None},
    ]
    out = mmr_select(cands, lambda_=0.7, top_k=2)
    assert [c["chunk_id"] for c in out] == ["a", "b"]
```

- [ ] **Step 2: 运行确认失败**

Expected: NULL 用例 ValueError；归一化用例选出 ["a","b"]。

- [ ] **Step 3: 重写 mmr.py 的矩阵构建段**

```python
    # 构建 embedding 矩阵：缺失（None）零填充，逐行 L2 归一化 → 内积即余弦。
    # 归一化在本函数内完成，不依赖上游承诺；零向量保持零。
    raw = [_embedding_to_list(c["embedding"]) for c in candidates]
    dim = max((len(v) for v in raw), default=0)
    if dim == 0:
        # 全部缺失 embedding：退化为纯相关性排序
        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        return [candidates[i] for i in ranked[:top_k]]
    emb_matrix = np.array(
        [v + [0.0] * (dim - len(v)) if len(v) < dim else v for v in raw],
        dtype=np.float32,
    )
    norms = np.linalg.norm(emb_matrix, axis=1)
    nonzero = norms > 0
    emb_matrix[nonzero] = emb_matrix[nonzero] / norms[nonzero, None]
```

（删除原 `emb_matrix = np.array([...])` 单行；docstring 中"Embedding vectors must be L2-normalized"改为"Embeddings are L2-normalized inside this function; missing embeddings are zero-filled."）

- [ ] **Step 4: 运行 + Commit**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_mmr.py -q
git add app/core/mmr.py tests/unit/test_mmr.py
git commit -m "fix(mmr): normalize rows in-function (true cosine), tolerate NULL embeddings"
```

---

### Task 3: doc_relation——量纲统一 + channel 3 独立发现

**Files:** `app/core/doc_relation.py`

- [ ] **Step 1: channel 1 分数除回 0–1**

```python
                if rel["relation_type"] == "complementary":
                    neighbor_scores[target] = max(
                        neighbor_scores[target], rel["cosine_scaled"] / 1000.0
                    )
```

- [ ] **Step 2: channel 3 重写为批量 + 独立发现**

替换原 channel 3 块：

```python
        # Channel 3: doc-level embedding cosine — 语义兜底，可独立发现文档。
        # 语料 ≤ _CH3_FULL_SCAN_LIMIT 时全量评估；更大时只对 ch1/ch2 候选求交（成本控制）。
        threshold = getattr(settings, "cross_doc_embedding_threshold", 0.7)
        if query_emb is not None:
            all_doc_ids = pgvector_store.get_all_doc_ids_with_entities(kb_ids)
            if len(all_doc_ids) <= _CH3_FULL_SCAN_LIMIT:
                ch3_candidates = [d for d in all_doc_ids if d not in matched_doc_ids]
            else:
                ch3_candidates = [d for d in list(neighbor_scores) if d not in matched_doc_ids]
            doc_embs = pgvector_store.get_doc_embeddings_bulk(ch3_candidates)
            for ndoc_id, doc_emb in doc_embs.items():
                cos_sim = _cosine_similarity(query_emb, doc_emb)
                if cos_sim >= threshold:
                    neighbor_scores[ndoc_id] = max(neighbor_scores[ndoc_id], cos_sim)
```

常量区加 `_CH3_FULL_SCAN_LIMIT = 200`。

- [ ] **Step 3: 提供同步孪生，async 版保留为兼容入口**

`retrieve` 重命名为 `retrieve_sync`（去掉 `async`），新增：

```python
    async def retrieve(self, *args, **kwargs) -> list[dict]:
        """兼容入口：内部全同步，调用方应优先用 retrieve_sync + to_thread。"""
        return self.retrieve_sync(*args, **kwargs)
```

- [ ] **Step 4: 更正模块与类 docstring**

把"will NOT block the event loop"改为"内部为同步 DB 调用；在事件循环中必须经 asyncio.to_thread（或等价手段）调用，否则阻塞整个 worker"。

- [ ] **Step 5: 运行既有 integration 保持绿**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_cross_doc.py tests/integration/test_retrieval_e2e.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/core/doc_relation.py
git commit -m "fix(cross-doc): unify channel score scale, channel-3 independent discovery, sync twin"
```

---

### Task 4: retrieval——to_thread、归一化映射、rerank 追加、零向量守卫

**Files:** `app/core/retrieval.py`, `tests/integration/test_cross_doc.py`, `tests/integration/test_retrieval_e2e.py`

- [ ] **Step 1: 新增失败测试**

`tests/integration/test_cross_doc.py` 追加：

```python
async def test_channel3_discovers_semantically_related_doc(ingest_docs, monkeypatch):
    """阈值放开到 0 时，channel 3 应能独立发现无词法交集的文档 3。"""
    from app.config import settings as s
    from app.core.doc_relation import cross_doc_retriever
    from app.store import pgvector_store

    monkeypatch.setattr(s, "cross_doc_embedding_threshold", 0.0)
    doc1 = ingest_docs["transformer_basics.md"]
    doc3 = ingest_docs["rag_chunking.md"]
    initial = pgvector_store.get_chunks_by_document(doc1)[:2]
    # 查询词与文档 3 无词法交集 → channel 1/2 不会发现它
    extras = await cross_doc_retriever.retrieve(
        "缩放点积公式推导", None if False else [0.1] * 4096,
        ["test-kb"], initial, can_read_all=True,
    )
    extra_docs = {c["document_id"] for c in extras}
    assert doc3 in extra_docs, "channel 3 未能独立发现语义相关文档"
```

`tests/integration/test_retrieval_e2e.py` 追加：

```python
async def test_rerank_partial_return_keeps_all_candidates(ingest_docs, monkeypatch):
    """reranker 只返回部分索引时，未返回候选应追加而非消失。"""
    from app.config import settings as s
    from app.llm.rerank import sf_rerank
    from app.core.retrieval import retrieval_engine

    monkeypatch.setattr(s, "mmr_enabled", False)
    monkeypatch.setattr(s, "rerank_top_k", 20)

    async def truncating_rerank(query, texts, **kw):
        return [{"index": i, "relevance_score": 1.0 - i * 0.1} for i in range(min(2, len(texts)))]

    monkeypatch.setattr(sf_rerank, "rerank", truncating_rerank)
    results = await retrieval_engine.retrieve("Transformer 多头注意力 QKV", None, can_read_all=True)
    # 语料三文档多 chunk：即使 rerank 只返回 2 个索引，最终结果也应 > 2
    assert len(results) > 2, "rerank 部分返回导致候选被静默丢弃"
```

- [ ] **Step 2: 转正 L1 xfail**

`test_cross_doc_extras_survive_tight_candidate_cut` 删除 xfail 装饰器。

- [ ] **Step 3: 运行确认三者失败**

- [ ] **Step 4: 修改 `app/core/retrieval.py`**

cross-doc 段整体替换：

```python
        # -- Cross-doc retrieval (three-channel jump) --
        cross_doc_extra_count = 0
        try:
            extra = await asyncio.to_thread(
                cross_doc_retriever.retrieve_sync,
                query, query_emb, target_kb_ids,
                results, user_role_ids, can_read_all,
            )
            if extra:
                cross_doc_extra_count = len(extra)
                # 归一化映射：附加 chunk 落在最强直连分的 70%~100% 区间，
                # 保留邻居间次序，最终位置交给 rerank/MMR——不再被 RRF 量纲压死
                max_neighbor = max(c["score"] for c in extra)
                max_original = max((r["score"] for r in results), default=0.3)
                for c in extra:
                    rel = c["score"] / max_neighbor if max_neighbor else 1.0
                    c["score"] = max_original * (0.7 + 0.3 * rel)
                results.extend(extra)
                results.sort(key=lambda x: x["score"], reverse=True)
                results = results[:candidate_k]
                seen_ids.update(c["chunk_id"] for c in extra)
                logger.info("cross_doc.extra_added count=%d", len(extra))
        except Exception:
            logger.exception("cross_doc.retrieve_failed")
```

rerank 应用段替换：

```python
                    if max_score - min_score > 0.001:
                        reranked_ids = [r["index"] for r in reranked if 0 <= r["index"] < len(results)]
                        reordered = [results[i] for i in reranked_ids]
                        returned = set(reranked_ids)
                        # reranker 未返回的候选按原序追加，不静默丢弃
                        reordered += [r for i, r in enumerate(results) if i not in returned]
                        results = reordered
```

embedding 降级段，`query_emb = [0.0] * settings.embedding_dimension` 两处之后各加守卫：

```python
            if not settings.hybrid_search_enabled:
                logger.warning("retrieve.pure_vector_degraded — embedding failed with hybrid off, returning []")
                return []
```

（放在 embed try/except 块之后、`seen_ids` 初始化之前，用 `embedding_degraded` 判断一次即可。）

- [ ] **Step 5: 运行确认通过**

```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_cross_doc.py tests/integration/test_retrieval_e2e.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/core/retrieval.py tests/integration/test_cross_doc.py tests/integration/test_retrieval_e2e.py
git commit -m "fix(retrieval): fair cross-doc score mapping, to_thread, rerank keeps all candidates"
```

---

### Task 5: pipeline 引用编号一致

**Files:** `app/core/pipeline.py`

- [ ] **Step 1: 移动 sources 事件**

把：

```python
        sources = _build_sources(unique_chunks)
        yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in sources])}\n\n"
```

整块从跨文档合并块**之前**移到**之后**（`yield cross_doc` 之后、`messages = prompt_builder.build_messages(...)` 之前）；`source_map` 填充逻辑保持在 sources 构建之前可及即可（跨文档块内 `source_map` 来自 sources——改为先用已有 Document 查询结果：`_build_sources` 之前跨文档块里 `g["filename"]` 的填充改用 doc_map 直接查，或把 `_build_sources` 拆为"解析 doc_map + 组装"两步。最小改法：跨文档块里保留 `g["filename"] = source_map.get(...)`，把 `sources = _build_sources(unique_chunks)` 提前到跨文档块前计算但**延迟 yield** 到合并后——sources 基于合并前列表会含被去重的 chunk，故必须在合并后重新 `_build_sources`：采用拆法——先 `doc_map = _resolve_doc_map(unique_chunks)`（从 `_build_sources` 提取），跨文档块用 doc_map 填 filename，合并后 `sources = _build_sources(unique_chunks); yield sources`。）

- [ ] **Step 2: 提取 `_resolve_doc_map`**

```python
def _resolve_doc_map(chunks: list[RetrievedChunk]) -> dict[str, str]:
    doc_ids = list({c.document_id for c in chunks if c.document_id})
    doc_map: dict[str, str] = {}
    if doc_ids:
        from app.store.db import get_db_ctx, Document
        with get_db_ctx() as session:
            rows = session.query(Document.document_id, Document.filename).filter(
                Document.document_id.in_(doc_ids)
            ).all()
            for row in rows:
                doc_map[row.document_id] = row.filename
    return doc_map
```

`_build_sources` 改为接受 `doc_map` 参数（不再内部查询）。

- [ ] **Step 3: 验证 import 链与全量套件**

```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
D:/miniConda/envs/rag/python.exe -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add app/core/pipeline.py
git commit -m "fix(pipeline): emit sources event after cross-doc merge so citations match UI"
```

---

### Task 6: 全量回归 + 收尾登记

- [ ] **Step 1: 全量运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest -q
```
Expected: `71 passed, 4 xfailed, 2 skipped`（基线 65 + 3 xfail 转正 + 新增 6 例 − 转正的 3 例已计入基线 xfail：65 passed 含原 xfailed 7 → 转正 3 后 68 passed/4 xfailed + 新增 3 例 = **71 passed, 4 xfailed, 2 skipped**）。任何 XPASS 必须排查。

- [ ] **Step 2: 更新 `docs/plans/README.md` 与本文件状态**（已完成 + commits）

- [ ] **Step 3: Commit**

```bash
git add docs/plans/
git commit -m "docs(plans): mark cross-doc-retrieval-overhaul complete + plan: cross-doc-retrieval-overhaul"
```

---

## Verification

| 验证项 | 命令 / 方式 | 期望 |
|---|---|---|
| 全量套件 | `D:/miniConda/envs/rag/python.exe -m pytest -q` | `71 passed, 4 xfailed, 2 skipped` |
| L1 修复验收 | `test_cross_doc_extras_survive_tight_candidate_cut`（去 marker） | passed |
| document_id 验收 | `test_get_chunks_by_document_includes_document_id`（去 marker） | passed |
| NULL embedding 验收 | `test_null_embedding_does_not_crash`（去 marker） | passed |
| channel 3 发现 | `test_channel3_discovers_semantically_related_doc` | passed |
| rerank 不丢候选 | `test_rerank_partial_return_keeps_all_candidates` | passed |
| bulk 上限 | `test_bulk_chunks_capped_per_doc` | 每文档 ≤10 |
| import 链 | `D:/miniConda/envs/rag/python.exe -c "import app.main"` | 退出码 0 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| `rebuild_all` / `update_for_document` 的 N+1 批量化 | 离线/摄入路径，非请求链路；收益与改动面不成比例 |
| BM25 fallback 的 rank 从 0 起算（RRF 轻微偏差） | 低危，仅 fallback 路径，不值得动核心 SQL |
| 邻居窗口扩展（±2）与已选 chunk 的文本去重 | 轻微 prompt 冗余，rerank_top_k 收口后影响有限；属 prompt 优化议题 |
| pipeline SSE 全链路自动化测试 | 需要流式 chat 的 fake 基建，随 `tag-stream-parser` plan 一并做 |
| 跨文档综合文本的 token 预算单独核算 | 现有 `chunk_budget` 裁剪仍生效；精细化预算属 `llm-gateway-convergence` |
| channel 2 的 `_QUERY_KEYWORD_MATCH_RATIO` 等阈值调参 | 本 plan 只修结构缺陷；阈值调优需要评测框架（README pending 项） |

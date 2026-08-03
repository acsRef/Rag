> 状态: 已完成（commits: bda6ede / 4c3a918 / T3 commit，分支 fix/ingestion-correctness）

# 摄入正确性（ingestion-correctness）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪；TDD：先失败测试再实现。

**Goal:** 修复文档摄入链路的正确性缺陷：questions 与 chunk 错位（zip 失配）、失败文档重试被 `unchanged` 短路、`index(document_id=None)` 的 FK 违反（xfail 转正）、全部 embedding 失败时误删旧索引、无 H3 文档坍缩成单 chunk、`_hard_split` 不递归、chunk_id 因 seq 位移产生孤儿问题行、诊断写盘可打断主流程、parser `get_image` 缺参。

**Architecture:** 三个原则：① 关联关系在构造点绑定（questions 随 chunks_data 构造时配对，不再事后 zip）；② Document 行先于 chunks 落库（满足 FK）；③ 失败保留旧索引（可重试恢复，不静默降级）。chunk_id 改为内容哈希派生的稳定 id，重索引时复用 chunk 的问题向量行不再孤儿化。

**Tech Stack:** pytest unit（chunker 纯逻辑）+ integration（indexer 全链路 + fake embedding 选择性失败）。

---

## Context

审查锁定的摄入缺陷（1 个 xfail 待转正）：

1. **zip 错位（高危）**：`indexer.py:281` `zip(chunks, chunks_data)`——embedding 失败的 chunk 被 `continue` 跳过后 `chunks_data` 变短，后续 chunk 的 questions 挂到前一个 chunk 上，多路召回张冠李戴且无日志。
2. **失败文档无法重试（高危）**：`unchanged` 判定只比 `content_hash` 不看 `status`——failed/partial 的文档用相同内容重试直接返回 unchanged，永远卡死。
3. **FK 违反（中危，xfail 锁定）**：`add_chunks` 先于 `_save_document` 执行，`index(document_id=None)` 在 FK schema 下必挂；生产靠 API 层预建行规避。
4. **全 embedding 失败误删旧索引（中危）**：重索引时新 chunk 全部 embedding 失败 → `replace_chunks(仅复用块)` 把仍有效的旧 chunk 一并删除，状态却标 partial。
5. **无 H3 坍缩（高危）**：`first_h3_idx = len(sections)` 时全文进 preamble 分支，合并为**单个 chunk 且不做尺寸检查**，绕过 `max_chunk_size` → embedding 截断、后半段永久不可检索。
6. **`_hard_split` 只切一刀（高危）**：section 超 2 倍上限时 `rest` 原样成 chunk，同样被截断。
7. **孤儿问题行（中危）**：chunk_id = `{doc_id}_{seq}`，前面删块导致 seq 位移 → 旧 chunk_questions 行指向错误内容。
8. **复用 chunk 的 LLM 标题被覆盖（中危）**：chunker 给每个 chunk 设了 section 标题，复用分支 `c.title or old` 让 section 标题盖掉 LLM 生成的标题。
9. **诊断写盘可打断摄入（中危）**：`_save_chunk_diag` 裸写磁盘未包 try，磁盘满/权限不足时 OSError 从 index() 逸出。
10. **parser 图片提取失效（中危）**：`pic.get_image()` 缺 docling 要求的 `DoclingDocument` 参数 → 每次 TypeError 被吞 → 内嵌图片描述功能静默失效。

## Design

### questions 对齐（indexer.py）

构造循环内在 `chunks_data.append` 的同一位置记录 `question_source.append((chunk_id, list(c.questions)))`（仅新 chunk；复用 chunk 的问题行随稳定 id 保留）。问题行构建阶段改用 `question_source`，删除 `zip(chunks, chunks_data)`。

### Document 行先行（indexer.py）

`doc_id` 确定后立即 `self._save_document(doc_id, ..., status="indexing", doc_hash)`（已有行则更新），chunks 持久化不再受 FK 阻塞；`index(document_id=None)` 成为合法路径。

### unchanged 守卫（indexer.py）

```python
if existing and existing.content_hash == doc_hash and existing.status == "indexed":
    return {... unchanged ...}
```

只有 `indexed` 算健康终态；failed/partial/indexing/pending_review 一律放行重索引。

### 全失败守卫（indexer.py）

持久化前：`total_new > 0 and embedded_count == len(old_chunks_map)`（新增块零成功）→ 状态 failed、**不动旧 chunks**、带错误信息返回。

### 稳定 chunk_id + 孤儿清理

- chunk_id = `{doc_id}_{content_hash[:10]}`；同文档重复内容追加 `_{n}` 后缀。复用 chunk 保持原 id → 其 chunk_questions 行继续有效。
- 新增 `pgvector_store.delete_orphan_chunk_questions(doc_id, valid_ids)`：删除 `{doc_id}_%` 前缀下不在 valid_ids 的行；持久化后调用兜底。

### chunker 尺寸闭环

- preamble 分支套用与 section 相同的尺寸检查：超限走 `_hard_split`。
- `_hard_split` 改迭代式切分：队列中片段超限就继续切，直到全部 ≤ `max_chunk_size`。

### 复用元数据优先

title/summary 字段复用分支优先旧值：`(is_reused and old.get("title")) or c.title or ""`（summary 同理）。

### 其余

- `_save_chunk_diag` 包 try/except（非致命，记日志）。
- parser：`_replace_embedded_images(md, pictures, doc)` 增加 doc 参数，`pic.get_image(doc)`。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| 单 chunk embedding 失败 | 该 chunk 跳过；questions 不错位；状态 partial |
| 新 chunk 全部 embedding 失败（重索引） | failed；旧索引原样保留，可重试 |
| failed 文档同内容重试 | 放行重索引（不再 unchanged） |
| 无 H3 长文档 | 按 max_chunk_size 切分 |
| 单 section 超 N 倍上限 | 迭代切分至全部 ≤ 上限 |
| 磁盘满 | 诊断写盘失败仅记日志，摄入继续 |
| 重复内容段落 | chunk_id 加序号后缀，不违反唯一约束 |

## Files to change

| 变更 | 路径 |
|---|---|
| Modify | `app/ingestion/indexer.py`（对齐/先行/守卫/稳定 id/复用元数据/diag 包裹）、`app/ingestion/chunker.py`（preamble 检查 + 迭代切分）、`app/ingestion/parser.py`（get_image 参数）、`app/store/pgvector_store.py`（孤儿清理） |
| Create | `tests/unit/test_chunker.py`（2 例） |
| Modify | `tests/integration/test_ingestion.py`（3 新例 + 1 xfail 转正）、`docs/plans/README.md` |

## Reused existing utilities

`_save_document`（先行调用即复用）、`_find_break_point`（迭代切分复用既有断点优先级）、fake embedding 层（conftest，选择性失败用 monkeypatch 覆盖）、`upsert_chunk_questions`（问题行写入路径不变）。

---

## Tasks

### Task 1: chunker 尺寸闭环

**Files:** `app/ingestion/chunker.py`, `tests/unit/test_chunker.py`

- [ ] **Step 1: 写失败测试**

```python
"""chunker 尺寸约束测试：无 H3 不坍缩、超限迭代切分。"""
from app.ingestion.chunker import TextChunker
from app.ingestion.structurer import document_structurer

NL = chr(10)


def test_no_h3_long_doc_split_by_size():
    text = "# 大标题" + NL + NL + ("这是一段没有任何小节的长文本。" * 30 + NL + NL) * 3
    sections = document_structurer.structure(text)
    chunks = TextChunker(max_chunk_size=300).chunk(sections)
    assert len(chunks) > 1, "无 H3 文档坍缩成了单 chunk"
    assert all(len(c.text) <= 300 for c in chunks)


def test_hard_split_recursive_on_oversized_section():
    md = NL.join(["# T", "## 章节", "### 小节", "长" * 1000])
    sections = document_structurer.structure(md)
    chunks = TextChunker(max_chunk_size=200).chunk(sections)
    assert len(chunks) >= 5
    assert all(len(c.text) <= 200 for c in chunks)
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`_hard_split` 迭代化：

```python
    def _hard_split(self, text: str, title: str, section_path: list[str]) -> list[Chunk]:
        result: list[Chunk] = []
        pending = [text]
        while pending:
            piece = pending.pop(0)
            if len(piece) <= self.max_chunk_size:
                if piece.strip():
                    result.append(Chunk(text=piece.strip(), title=title, section_path=list(section_path)))
                continue
            end = self._find_break_point(piece, self.max_chunk_size)
            first = piece[:end].strip()
            rest = piece[end:].strip()
            if first:
                result.append(Chunk(text=first, title=title, section_path=list(section_path)))
            if rest:
                pending.append(rest)
        return result
```

preamble 分支加尺寸检查：

```python
        if preamble_elems:
            chunk_text = self._build_chunk_text(preamble_elems, preamble_path)
            title_val = preamble_path[0] if preamble_path else ""
            if len(chunk_text) > self.max_chunk_size:
                chunks[:0] = self._hard_split(chunk_text, title_val, preamble_path)
            else:
                chunks.insert(
                    0,
                    Chunk(text=chunk_text, title=title_val, section_path=list(preamble_path)),
                )
```

- [ ] **Step 4: 运行 + Commit**

```bash
git add app/ingestion/chunker.py tests/unit/test_chunker.py
git commit -m "fix(chunker): size-check preamble, iterate hard-split until under limit + plan: ingestion-correctness"
```

---

### Task 2: indexer 对齐 + 守卫 + 稳定 id

**Files:** `app/ingestion/indexer.py`, `app/store/pgvector_store.py`, `tests/integration/test_ingestion.py`

- [ ] **Step 1: 追加失败测试**

```python
async def test_failed_doc_can_be_retried(integration_db, monkeypatch):
    """失败文档用相同内容重试必须真正重索引（旧逻辑 unchanged 短路，永远卡死）。"""
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding

    doc_id = _precreate_document_row("retry.md")
    content = "# 重试\n\n这是用于重试测试的内容。\n".encode("utf-8")

    async def all_fail(texts, **kw):
        return [(None, "模拟失败") for _ in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", all_fail)
    res1 = document_indexer.index("retry.md", content, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res1["status"] == "failed"

    monkeypatch.undo()   # 恢复 conftest 的正常 fake
    res2 = document_indexer.index("retry.md", content, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res2["status"] == "indexed", "失败文档重试被 unchanged 短路"


async def test_questions_align_with_persisted_chunks(integration_db, monkeypatch):
    """单 chunk embedding 失败时，questions 不得挂到别的 chunk（旧 zip 错位）。"""
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding
    from app.store import pgvector_store
    from app.store.db import ChunkQuestion, get_db_ctx

    async def selective_fail(texts, **kw):
        from tests.integration.conftest import fake_vector
        return [(None, "模拟失败") if "乙段失败标记" in t else (fake_vector(t), None)
                for t in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", selective_fail)
    doc_id = _precreate_document_row("align.md")
    md = chr(10).join([
        "# 对齐测试",
        "### 甲", "甲段内容文字。",
        "### 乙", "乙段失败标记内容。",
        "### 丙", "丙段内容文字。",
    ])
    res = document_indexer.index("align.md", md.encode("utf-8"), kb_id="test-kb",
                                 user_id="test-user", document_id=doc_id)
    assert res["status"] == "partial"

    chunks = {c["chunk_id"]: c["text"] for c in pgvector_store.get_chunks_by_document(doc_id)}
    assert len(chunks) == 2                      # 乙段被跳过
    with get_db_ctx() as session:
        rows = session.query(ChunkQuestion).filter(
            ChunkQuestion.chunk_id.like(doc_id + "%")).all()
    assert rows
    for r in rows:
        assert r.chunk_id in chunks
        marker = "甲" if "甲" in r.question else "丙"
        assert marker in chunks[r.chunk_id], "questions 挂错 chunk（zip 错位）"


async def test_all_embed_failed_keeps_old_index(integration_db, monkeypatch):
    """重索引时新 chunk 全部 embedding 失败 → failed 且旧索引原样保留。"""
    from app.ingestion.indexer import document_indexer
    from app.llm.embedding import sf_embedding
    from app.store import pgvector_store

    doc_id = _precreate_document_row("keepold.md")
    v1 = "# 保留\n\n第一版内容。\n".encode("utf-8")
    res1 = document_indexer.index("keepold.md", v1, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res1["status"] == "indexed"
    n_before = len(pgvector_store.get_chunks_by_document(doc_id))

    async def all_fail(texts, **kw):
        return [(None, "模拟失败") for _ in texts]

    monkeypatch.setattr(sf_embedding, "embed_with_fallback", all_fail)
    v2 = "# 保留\n\n第一版内容。\n\n### 新增\n\n第二版新增内容。\n".encode("utf-8")
    res2 = document_indexer.index("keepold.md", v2, kb_id="test-kb",
                                  user_id="test-user", document_id=doc_id)
    assert res2["status"] == "failed"
    assert len(pgvector_store.get_chunks_by_document(doc_id)) == n_before
```

（`_precreate_document_row` 已在该文件存在。）

- [ ] **Step 2: 运行确认失败**（retry 被 unchanged 短路；对齐测试 questions 挂错；keepold 旧索引被删）

- [ ] **Step 3: 实现 `app/store/pgvector_store.py` 孤儿清理**

```python
def delete_orphan_chunk_questions(document_id: str, valid_chunk_ids: list[str]) -> None:
    """删除指定文档下不再存在的 chunk 的问题行（chunk_id 位移/删除后兜底）。"""
    session = get_session()
    try:
        q = session.query(ChunkQuestion).filter(
            ChunkQuestion.chunk_id.like(document_id + "_%"))
        if valid_chunk_ids:
            q = q.filter(~ChunkQuestion.chunk_id.in_(valid_chunk_ids))
        q.delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
```

- [ ] **Step 4: 实现 `app/ingestion/indexer.py`**

改动点（按顺序）：

1. unchanged 守卫加状态条件：

```python
            if (existing and existing.content_hash == doc_hash
                    and existing.status == "indexed"):
```

2. `doc_id = document_id or new_id()` 之后立即预建行：

```python
        # Document 行先于 chunks 落库：chunks 外键引用 documents，
        # 旧顺序在 index(document_id=None) 时必然 FK 违反
        self._save_document(doc_id, user_id, kb_id, filename, 0, "indexing", doc_hash)
```

3. `_save_chunk_diag` 调用包 try：

```python
        try:
            self._save_chunk_diag(doc_id, filename, sections, chunks)
        except OSError:
            logger.exception("chunk diag save failed (non-fatal) doc=%s", doc_id[:8])
```

4. 构造循环：稳定 id + question_source（替换 `chunk_id = f"{doc_id}_{chunk_seq}"; chunk_seq += 1`）：

```python
        chunk_seq = 0          # 仅用于重复内容后缀
        ...（循环内）
            ch = c.content_hash
            base_id = "%s_%.10s" % (doc_id, ch)
            n = seen_chunk_ids.get(base_id, 0)
            seen_chunk_ids[base_id] = n + 1
            chunk_id = base_id if n == 0 else "%s_%d" % (base_id, n)
```

（循环前初始化 `seen_chunk_ids: dict[str, int] = {}`、`question_source: list[tuple[str, list[str]]] = []`；`chunks_data.append` 之后对非复用 chunk 记录 `question_source.append((chunk_id, list(c.questions or [])))`。）

5. title/summary 复用优先：

```python
                "title": (is_reused and old.get("title")) or c.title or "",
                "summary": (is_reused and old.get("summary")) or c.summary or "",
```

6. 全失败守卫（status 计算后、持久化前）：

```python
        # 新 chunk 全部 embedding 失败：保留旧索引，直接 failed（可重试恢复）
        if total_new > 0 and embedded_count == len(old_chunks_map):
            self._save_document(doc_id, user_id, kb_id, filename, len(chunks), "failed", doc_hash,
                                embedded_chunk_count=embedded_count,
                                error_message=final_error or "所有新增分块向量化失败，保留旧索引，请重试")
            return {
                "document_id": doc_id,
                "filename": filename,
                "status": "failed",
                "chunk_count": len(chunks),
            }
```

7. 问题行构建改用 question_source（替换 zip 循环）：

```python
            # 问题行与 chunk_id 在构造点绑定——旧 zip(chunks, chunks_data)
            # 在 embedding 失败跳块后错位，把问题挂到错误的 chunk
            question_data = []
            for cd_id, qs in question_source:
                for pos, q in enumerate(qs):
                    if q.strip():
                        question_data.append({
                            "chunk_id": cd_id,
                            "question": q,
                            "position": pos,
                        })
```

8. 持久化后孤儿清理（upsert_chunk_questions 之后）：

```python
            pgvector_store.delete_orphan_chunk_questions(
                doc_id, [cd["chunk_id"] for cd in chunks_data])
```

- [ ] **Step 5: 运行 integration + 转正 xfail**

删除 `test_index_without_precreated_document_row` 的 xfail 装饰器（Document 行先行后该路径合法）。

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_ingestion.py -q
```
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/indexer.py app/store/pgvector_store.py tests/integration/test_ingestion.py
git commit -m "fix(indexer): align questions at construction, early document row, retry & keep-old guards, stable chunk ids"
```

---

### Task 3: parser get_image 参数

**Files:** `app/ingestion/parser.py`

- [ ] **Step 1: 传入 DoclingDocument**

```python
            if result.document.pictures:
                md = self._replace_embedded_images(md, result.document.pictures, result.document)
```

```python
    def _replace_embedded_images(self, md: str, pictures, doc) -> str:
        ...
                pil_img = pic.get_image(doc)
```

- [ ] **Step 2: 验证 import 链 + Commit**

```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
git add app/ingestion/parser.py
git commit -m "fix(parser): pass DoclingDocument to PictureItem.get_image (embedded image extraction was dead)"
```

---

### Task 4: 全量回归 + 收尾

- [ ] **Step 1: 全量运行**

Run: `D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: `102 passed, 0 xfailed, 2 skipped`（96 基线 + chunker 2 + ingestion 3 + xfail 转正 1 = 102，xfail 清零）。

- [ ] **Step 2: 更新 plan 状态与索引（本 plan 转已完成；「暂缓/待细化」表清空），Commit**

```bash
git add docs/plans/
git commit -m "docs(plans): mark ingestion-correctness complete + plan: ingestion-correctness"
```

## Verification

| 验证项 | 期望 |
|---|---|
| 全量套件 | `102 passed, 0 xfailed, 2 skipped` |
| FK xfail 转正 | `test_index_without_precreated_document_row` passed |
| 错位回归 | `test_questions_align_with_persisted_chunks` passed |
| 重试回归 | `test_failed_doc_can_be_retried` passed |
| 保旧索引 | `test_all_embed_failed_keeps_old_index` passed |
| chunker 尺寸 | `tests/unit/test_chunker.py` 2 passed |
| import 链 | `D:/miniConda/envs/rag/python.exe -c "import app.main"` 退出码 0 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| atomic 块（代码/表格/图片）真正不可切分 | 需要按 Element 粒度重构 chunker（当前 _build_chunk_text 已把元素拍平成文本）；尺寸闭环先解决截断这一实际损害，atomic 语义另立 plan |
| structurer 标题黑名单子串误杀（"目录结构说明"等） | 属检索质量调优，需要语料评测支撑；与 README pending 的评估框架一并做 |
| 复用 chunk 问题向量的缺失重建 | 稳定 id 后复用 chunk 的问题行保持有效；仅历史 seq-id 数据一次性失效，可接受 |
| `app.api.documents.emit_doc_progress` 反向依赖解耦 | 结构性重构，收益与改动面不成比例；现状 try/except 已兜底 |
| metadata 单次调用不分批（大文档输出截断） | 需要分批策略与 LLM 输出预算设计，独立议题 |

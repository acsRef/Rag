# RAG v2 实施计划 — Embedding 配置迁移 + 表格感知摄入（Issue #2 第一期）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按单变量归因纪律完成 embedding 配置迁移（Qwen3-VL-Embedding-8B@4096 → Qwen3-Embedding-8B@1024）与表格感知摄入第一期，产出 Baseline-1R/2/3/4 四级可对比基线。

**Architecture:** 先代码后数据——Phase A 纯代码准备（cache key 版本化、API dimensions 显式传参、DB 维度单点驱动 + 表格列、双脚本分离、摄入侧 question 门控、评测工具），Phase B 在旧库上重放 Baseline-1R（先于一切破坏性操作），Phase C/D/E 依次执行迁移→表格 schema→question channel，每步以 15 题 verified 快测把关并锁定 baseline 文档。

**Tech Stack:** FastAPI + SQLAlchemy/pgvector 0.8 + PostgreSQL 15；Python 3.11 conda env `rag`（一律用绝对路径 `D:/miniConda/envs/rag/python.exe`）；pytest + ruff；eval 工具走 HTTP API 对运行中的后端。

**Spec:** [2026-08-23-embedding-migration-table-aware-design.md](2026-08-23-embedding-migration-table-aware-design.md)

---

## 关键事实卡（执行者必读）

```text
python      = D:/miniConda/envs/rag/python.exe     # 永远用这个，别用其他 env
pytest      = D:/miniConda/envs/rag/python.exe -m pytest
ruff        = D:/miniConda/envs/rag/python.exe -m ruff check app/ tests/
测试基线     = 546 passed / 6 failed / 13 skipped
6 failed 预存集（禁止新增，允许原样存在）：
  tests/integration/test_cross_doc.py::test_cross_doc_extras_reach_final_results
  tests/integration/test_ingestion.py::test_reindex_partial_embed_failure_preserves_old_index
  tests/integration/test_ingestion.py::test_failed_doc_can_be_retried
  tests/integration/test_ingestion.py::test_questions_align_with_persisted_chunks
  tests/unit/test_chunker.py::test_oversized_section_packs_on_element_boundaries
  tests/integration/test_retrieval_year_coverage.py::test_supplement_appends_missing_related_years  # 位于 tests/unit/
dev 库       = postgresql://ragent:ragent@localhost:5432/ragent（docker compose 起，容器名 ragent-postgres）
测试库       = ragent_test（integration 自动建，勿动 dev 库）
KB          = 名字含"三一重工"的库（脚本动态查找；当前 id 7e88fe99ccf541f3）
PDF         = eval/sany_annual_reports/三一重工_{2023,2024,2025}年年度报告.pdf
后端         = D:/miniConda/envs/rag/python.exe -m app.main  （端口 8000）
默认管理员   = admin / admin123
Evaluator   = LOCKED v1 —— judge prompt 一律复用 eval/eval_sany.py 的 JUDGE_PROMPT + judge_answer，
              gold 一律经 eval/gold.py 读取（禁止直接 open rag_testset.json）
db.py 约束   = 本次有意豁免"不修改 db.py"条款（spec §决策表），commit message 必须显式声明
ruff 现状    = master 上 eval/ 有预存 lint 噪声（audit_conflict_v3.py 等）；本计划只要求
              app/ + tests/ + 本次新建 eval 文件全绿，不顺手修无关文件
```

## 文件结构总览

| 动作 | 路径 | 职责 |
| --- | --- | --- |
| 改 | app/config.py | embedding 模型/维度/send_dimensions 开关/current_embedding_version |
| 改 | app/llm/embedding.py | 3 处调用点显式传 dimensions |
| 改 | app/core/cache.py | EmbeddingCache key 加 model+dim+input_version |
| 改 | app/store/db.py | 4 处 Vector 维度单点驱动 + chunks 3 个表格列（ORM+raw SQL 双同步）|
| 改 | app/store/pgvector_store.py | add_chunks 补写 embedding_text 等字段；两路径都写表格列 |
| 改 | app/ingestion/indexer.py | 摄入侧 question 门控；embed 输入切到 retrieval_text；写表格元数据 |
| 建 | app/ingestion/retrieval_text.py | build_retrieval_text() 纯函数：表格行展开归一化 |
| 建 | tools/migrate_vector_dimension.py | 只管 schema 迁移（format_type 检测、空表才 ALTER）|
| 建 | tools/reset_rag_corpus.py | 只管数据销毁（显式表清单、RESTART IDENTITY、--yes）|
| 建 | eval/baseline_eval.py | 15 题 verified 快测 runner（locked judge + gold 单一入口）|
| 建 | tools/index_integrity_report.py | 重插后完整性硬断言 |
| 改 | tests/integration/{test_cross_doc,test_search_errors,test_ingestion}.py | 去 4096 硬编码 |
| 建 | tests/unit/test_retrieval_text.py 等 | 新逻辑单测 |

---

# Phase A — 代码准备（不动任何数据库数据）

### Task 1: EmbeddingCache key 版本化（correctness fix）

**Files:**
- Modify: `app/core/cache.py`
- Test: `tests/unit/test_cache.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_cache.py` 的 `# ── RetrievalCache ──` 分隔注释之前插入：

```python
# ── EmbeddingCache key 隔离（model/dim/input_version）────────


def test_embedding_cache_isolates_by_model():
    cache_a = EmbeddingCache(model="m-a", dimension=1024)
    cache_b = EmbeddingCache(model="m-b", dimension=1024)
    cache_a.set("hello", [1.0])
    assert cache_b.get("hello") is None  # 不同模型不互 hit


def test_embedding_cache_isolates_by_dimension():
    cache_a = EmbeddingCache(model="m", dimension=4096)
    cache_b = EmbeddingCache(model="m", dimension=1024)
    cache_a.set("hello", [0.0] * 4096)
    assert cache_b.get("hello") is None  # 同文本不同维度绝不能互 hit（correctness）


def test_embedding_cache_isolates_by_input_version():
    cache_v1 = EmbeddingCache(model="m", dimension=1024, input_version=1)
    cache_v2 = EmbeddingCache(model="m", dimension=1024, input_version=2)
    cache_v1.set("hello", [1.0])
    assert cache_v2.get("hello") is None


def test_embedding_cache_same_config_hits():
    cache_a = EmbeddingCache(model="m", dimension=1024, input_version=1)
    cache_b = EmbeddingCache(model="m", dimension=1024, input_version=1)
    cache_a.set("hello", [1.0, 2.0])
    assert cache_b.get("hello") == [1.0, 2.0]
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_cache.py -v -k isolates`
Expected: FAIL — `EmbeddingCache() got an unexpected keyword argument 'model'`

- [ ] **Step 3: 最小实现**

修改 `app/core/cache.py`：

```python
# 模块级常量（放在 import 之后、类定义之前）：
# embedding 输入表示版本：1 = 原始 chunk.text；表格归一化检索表示上线时 bump 到 2。
# 参与 EmbeddingCache key——同 model/dim 下输入表示变更必须使旧缓存失效。
EMBEDDING_INPUT_VERSION = 1
```

`EmbeddingCache` 改为：

```python
class EmbeddingCache:
    """text → vec 的 LRU 缓存；满后淘汰最久未访问。

    key = sha256(model \0 dimension \0 input_version \0 text)。
    换 embedding 配置（模型/维度）或输入表示升级时，同文本的旧缓存向量
    维度/语义都可能错——key 必须把这三者编进去（correctness，非优化）。
    """

    def __init__(
        self,
        max_size: int = 4096,
        *,
        model: str = "",
        dimension: int = 0,
        input_version: int = EMBEDDING_INPUT_VERSION,
    ):
        self.max_size = max_size
        self._store: OrderedDict[str, list[float]] = OrderedDict()
        self._prefix = f"{model}\x00{dimension}\x00{input_version}\x00"

    def _key(self, text: str) -> str:
        # sha256 抗碰撞；utf-8 编码保证跨平台一致
        return hashlib.sha256((self._prefix + text).encode("utf-8")).hexdigest()
```

（`get`/`set`/`clear`/`__len__` 不动——它们已调用 `self._key`。）

文件底部全局实例改为：

```python
embedding_cache = EmbeddingCache(
    model=settings.embedding_model,
    dimension=settings.embedding_dimension,
)
```

顶部 import 增加 `from app.config import settings`（注意 cache.py 当前无此 import，需新增且保持 import 排序过 ruff）。

- [ ] **Step 4: 运行确认通过**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_cache.py tests/unit/test_embedding_cache_integration.py -v`
Expected: 全 PASS（旧测试构造 `EmbeddingCache()` 用默认参数，不受影响）

- [ ] **Step 5: Commit**

```bash
git add app/core/cache.py tests/unit/test_cache.py
git commit -m "fix(cache): version EmbeddingCache key with model+dimension+input_version

Same text under a different embedding config previously collided on the
text-only hash and could return a wrong-dimension vector. Key now binds
model, dimension, and EMBEDDING_INPUT_VERSION (correctness fix, spec §4.2c)."
```

---

### Task 2: Embedding client 显式传 dimensions

**Files:**
- Modify: `app/llm/embedding.py`
- Modify: `app/config.py`（仅加一个开关字段）
- Test: `tests/unit/test_embed_fallback.py`

- [ ] **Step 1: config 加开关**

`app/config.py` 在 `embedding_rate_limit_rps` 之后加：

```python
    embedding_send_dimensions: bool = True  # env: EMBEDDING_SEND_DIMENSIONS
    # Qwen3-Embedding 系列接受 dimensions 参数；bge-m3 等固定维度模型不支持，
    # 切换此类模型时置 false（其原生维度写入 embedding_dimension）。
```

- [ ] **Step 2: 写失败测试**

在 `tests/unit/test_embed_fallback.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_embed_passes_explicit_dimensions(monkeypatch):
    """API 调用必须显式携带 dimensions=settings.embedding_dimension。"""
    captured = {}

    class _RespData:
        embedding = [0.1] * settings.embedding_dimension

    class _Resp:
        data = [_RespData()]

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _Resp()

    class _FakeEmbeddings:
        create = staticmethod(fake_create)

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    from app.llm import embedding as emb_mod

    monkeypatch.setattr(emb_mod.sf_embedding, "_client", _FakeClient())
    monkeypatch.setattr(emb_mod.sf_embedding, "_client_loop_id", 123)
    monkeypatch.setattr(settings, "embedding_send_dimensions", True)

    await emb_mod.sf_embedding.embed("维度契约测试")
    assert captured.get("dimensions") == settings.embedding_dimension


@pytest.mark.asyncio
async def test_embed_omits_dimensions_when_disabled(monkeypatch):
    captured = {}

    class _RespData:
        embedding = [0.1] * 4

    class _Resp:
        data = [_RespData()]

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _Resp()

    class _FakeEmbeddings:
        create = staticmethod(fake_create)

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    from app.llm import embedding as emb_mod

    monkeypatch.setattr(emb_mod.sf_embedding, "_client", _FakeClient())
    monkeypatch.setattr(emb_mod.sf_embedding, "_client_loop_id", 123)
    monkeypatch.setattr(settings, "embedding_send_dimensions", False)

    await emb_mod.sf_embedding.embed("关闭开关测试")
    assert "dimensions" not in captured
```

（若该文件缺 `import pytest` / `settings` 导入则补上，与文件现有风格一致。）

- [ ] **Step 3: 运行确认失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_embed_fallback.py -v -k dimensions`
Expected: FAIL — `captured.get("dimensions")` 为 None / `"dimensions" in captured` 为 False

- [ ] **Step 4: 实现——三处调用点统一改造**

`app/llm/embedding.py` 新增模块级 helper（放 `RateLimiter` 类之前）：

```python
def _embed_kwargs(text: str | list[str]) -> dict:
    """构造 embeddings.create 的参数。dimensions 显式传参是配置契约：
    config → API → DB schema 三处维度必须一致（spec §4.2b）。"""
    kwargs: dict = {"model": settings.embedding_model, "input": text}
    if settings.embedding_send_dimensions:
        kwargs["dimensions"] = settings.embedding_dimension
    return kwargs
```

三处替换：
1. `embed()` 内 `resp = await self.client.embeddings.create(model=self.model, input=text)` → `resp = await self.client.embeddings.create(**_embed_kwargs(text))`
2. `embed_single_chunk()` 内同样模式替换
3. `_try_batch_with_retry()` 内 `resp = await sf.client.embeddings.create(model=sf.model, input=texts)` → `resp = await sf.client.embeddings.create(**_embed_kwargs(texts))`

- [ ] **Step 5: 运行确认通过 + 全量守口**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_embed_fallback.py -v && D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q`
Expected: 新测试 PASS；unit 层无新增 fail

- [ ] **Step 6: Commit**

```bash
git add app/llm/embedding.py app/config.py tests/unit/test_embed_fallback.py
git commit -m "feat(embed): send explicit dimensions=<config> on every embeddings call

Contract: API output dim must equal settings.embedding_dimension.
Gated by EMBEDDING_SEND_DIMENSIONS for fixed-dim models (bge-m3)."
```

---

### Task 3: db.py 维度单点驱动 + 表格 metadata 列（受控豁免）

**Files:**
- Modify: `app/store/db.py`
- Modify: `app/store/pgvector_store.py`
- Modify: `tests/integration/test_cross_doc.py`, `tests/integration/test_search_errors.py`, `tests/integration/test_ingestion.py`

**注意：本任务改 CLAUDE.md 标注不可修改的 db.py —— spec 已声明豁免，commit message 必须含 "exemption per spec" 字样。**

- [ ] **Step 1: 测试去硬编码（先行，保证后续每步可跑）**

三处替换：
- `tests/integration/test_cross_doc.py:136`：`[0.1] * 4096` → `[0.1] * settings.embedding_dimension`（文件顶补 `from app.config import settings`）
- `tests/integration/test_search_errors.py:17` 与 `:66`：同上替换
- `tests/integration/test_ingestion.py:18`：`assert len(c["embedding"]) == 4096` → `assert len(c["embedding"]) == settings.embedding_dimension`

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/integration -q`
Expected: 仍为预存 fail 集（4 个），无新增

- [ ] **Step 2: ORM 三处 + raw SQL 一处改 settings 驱动**

`app/store/db.py`：
1. L143 raw SQL（`CREATE TABLE IF NOT EXISTS chunk_questions`）改为 f-string：

```python
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS chunk_questions ("
                    f"id SERIAL PRIMARY KEY, "
                    f"chunk_id VARCHAR(64) REFERENCES chunks(chunk_id) ON DELETE CASCADE, "
                    f"question TEXT NOT NULL, "
                    f"embedding VECTOR({settings.embedding_dimension}), "
                    f"position INT DEFAULT 0)"
                )
            )
```

2. `ChunkQuestion.embedding`（L289）：`Column(Vector(4096))` → `Column(Vector(settings.embedding_dimension))`
3. `Chunk.embedding`（L306）：同上
4. `DocEmbedding.embedding`（L432）：同上

同时在 init_db 的 ALTER 区（`figure_title` 那条之后）追加三个新列 + 注释：

```python
            # ── 表格感知摄入列（Issue #2 第一期；本期只入库，检索层二期消费）──
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_type VARCHAR(16)"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS table_headers TEXT[]"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS table_meta JSONB"))
```

ORM `Chunk` 类在 `figure_title` 之后追加：

```python
    # Issue #2 第一期：表格 chunk 元数据（text=paragraph | table）；本期只入库
    chunk_type = Column(String(16), nullable=True)
    table_headers = Column(ARRAY(Text), nullable=True)
    table_meta = Column(JSON, nullable=True)
```

（`ARRAY`/`JSON` 已在该文件 import。）

- [ ] **Step 3: pgvector_store 两路径补写字段**

`app/store/pgvector_store.py`：
- `add_chunks()`（L32-64）：docstring 字段清单加 `embedding_text, embedding_version, chunk_type, table_headers, table_meta`；`Chunk(...)` 构造中 `search_text=...` 行之后补：

```python
                    embedding_text=c.get("embedding_text"),
                    embedding_version=c.get("embedding_version", 1),
                    chunk_type=c.get("chunk_type"),
                    table_headers=c.get("table_headers"),
                    table_meta=c.get("table_meta"),
```

- `replace_chunks()` 的 `values = dict(...)` 中（已有 embedding_text/embedding_version）补：

```python
                chunk_type=c.get("chunk_type"),
                table_headers=c.get("table_headers"),
                table_meta=c.get("table_meta"),
```

- [ ] **Step 4: import chain + 全量测试守口**

Run: `D:/miniConda/envs/rag/python.exe -c "import app.main" && D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: import 成功；`546 passed / 6 failed / 13 skipped` 不变

- [ ] **Step 5: ruff**

Run: `D:/miniConda/envs/rag/python.exe -m ruff check app/ tests/ && D:/miniConda/envs/rag/python.exe -m ruff format --check app/core/cache.py app/config.py app/llm/embedding.py app/store/db.py app/store/pgvector_store.py tests/`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add app/store/db.py app/store/pgvector_store.py tests/integration/test_cross_doc.py tests/integration/test_search_errors.py tests/integration/test_ingestion.py
git commit -m "refactor(db): single-source vector dims from settings + table metadata cols

Exemption per spec (2026-08-23-embedding-migration-table-aware-design §决策表):
Vector(4096) x3 ORM + raw-SQL VECTOR(4096) -> settings.embedding_dimension
(config->client->DB configuration contract). Adds chunks.chunk_type /
table_headers / table_meta via idempotent ADD COLUMN IF NOT EXISTS, synced
across ORM and raw SQL. add_chunks() now persists embedding_text +
embedding_version (parity with replace_chunks). Tests de-hardcode 4096."
```

---

### Task 4: 迁移与清库双脚本

**Files:**
- Create: `tools/migrate_vector_dimension.py`
- Create: `tools/reset_rag_corpus.py`
- Create: `tests/unit/test_vector_tools.py`

- [ ] **Step 1: 写纯函数部分的失败测试**

创建 `tests/unit/test_vector_tools.py`：

```python
"""migrate/reset 两个运维脚本的纯逻辑单测（DB 交互 mock）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))

from migrate_vector_dimension import parse_vector_type  # noqa: E402


def test_parse_vector_type_extracts_dimension():
    assert parse_vector_type("vector(4096)") == 4096
    assert parse_vector_type("vector(1024)") == 1024


def test_parse_vector_type_none_for_non_vector():
    assert parse_vector_type("integer") is None
    assert parse_vector_type("text") is None
    assert parse_vector_type("character varying(64)") is None
    assert parse_vector_type(None) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_vector_tools.py -v`
Expected: FAIL — `No module named 'migrate_vector_dimension'`

- [ ] **Step 3: 实现 migrate 脚本**

创建 `tools/migrate_vector_dimension.py`：

```python
"""向量列维度迁移 —— 只管 schema，不管数据销毁（清库请用 reset_rag_corpus.py）。

对 chunks / chunk_questions / doc_embeddings 三表的 embedding 列逐一检测：
- 当前维度 == 目标 → skip
- 行数 > 0 → ABORT（要求先清库；空表 ALTER 才是瞬时安全的）
- 否则 ALTER TYPE vector(:target)

用法：
    D:/miniConda/envs/rag/python.exe tools/migrate_vector_dimension.py --target 1024 [--apply]

不带 --apply 时 dry-run，只打印检测结论。
维度检测用 format_type() 读显示串再解析，不依赖 pgvector typmod 编码细节。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.store.db import engine  # noqa: E402

TABLES = ("chunks", "chunk_questions", "doc_embeddings")


def parse_vector_type(type_str: str | None) -> int | None:
    """'vector(4096)' → 4096；非 vector 类型返回 None。"""
    if not type_str:
        return None
    m = re.fullmatch(r"\s*vector\s*\(\s*(\d+)\s*\)\s*", type_str)
    return int(m.group(1)) if m else None


def get_column_type(engine, table: str, column: str = "embedding") -> str | None:
    row = engine.connect().execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a "
            "WHERE a.attrelid = (:t)::regclass AND a.attname = :c AND a.attnum > 0"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def get_row_count(engine, table: str) -> int:
    return engine.connect().execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=None, help="目标维度（默认取 settings.embedding_dimension）")
    parser.add_argument("--apply", action="store_true", help="真正执行 ALTER（默认 dry-run）")
    args = parser.parse_args()

    target = args.target if args.target is not None else settings.embedding_dimension
    fatal = False

    with engine.begin() as conn:
        for table in TABLES:
            cur_type = get_column_type(conn, table)
            cur_dim = parse_vector_type(cur_type)
            rows = get_row_count(conn, table)
            status = "SKIP(已是目标维度)" if cur_dim == target else "ALTER"
            print(f"{table}: type={cur_type} rows={rows} -> {status}")

            if cur_dim is None:
                print(f"  ABORT: {table}.embedding 不是 vector 类型（{cur_type}）")
                fatal = True
                continue
            if cur_dim == target:
                continue
            if rows > 0:
                print(f"  ABORT: {table} 有 {rows} 行——先跑 tools/reset_rag_corpus.py 清库")
                fatal = True
                continue
            if args.apply:
                conn.execute(
                    text(f'ALTER TABLE "{table}" ALTER COLUMN embedding TYPE vector({target})')
                )
                print(f"  ALTERED -> vector({target})")

    if fatal:
        print("\n存在 ABORT 项，未完成全部迁移")
        return 1
    if not args.apply:
        print("\n(dry-run 完成；加 --apply 执行)")
    else:
        print(f"\n迁移完成：全部 embedding 列 -> vector({target})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

（注：`get_column_type` 里 `engine.connect().execute(...)` 若 SQLAlchemy 2.x 报驱动差异，改为在 `main` 的 `conn` 上直接调 `conn.execute(text(...))`——实现时以能跑为准，签名不变。）

- [ ] **Step 4: 实现 reset 脚本**

创建 `tools/reset_rag_corpus.py`：

```python
"""清空语料 + 会话数据 —— 数据销毁专用，与 schema 迁移(migrate_vector_dimension.py)分离。

单语句多表 TRUNCATE（免 CASCADE 且满足互引 FK）+ RESTART IDENTITY。
保留 users / roles / permissions / user_roles / knowledge_bases / kb_role_access /
sensitive_rules / pii_alerts / pii_hold / dim_* / fact_*。

用法：
    D:/miniConda/envs/rag/python.exe tools/reset_rag_corpus.py           # dry-run 打印将清对象与行数
    D:/miniConda/envs/rag/python.exe tools/reset_rag_corpus.py --yes     # 真正执行
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text  # noqa: E402

from app.store.db import engine  # noqa: E402

TRUNCATE_TABLES = (
    # 会话侧（messages←conversations FK）
    "messages",
    "conversations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    # 语料侧（chunks←documents/doc_role_access 互引，单语句多表处理）
    "chunk_questions",
    "chunks",
    "doc_role_access",
    "documents",
    "doc_embeddings",
    "doc_entities",
    "doc_relations",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="真正执行（默认 dry-run）")
    args = parser.parse_args()

    stmt = text(f'TRUNCATE TABLE {", ".join(TRUNCATE_TABLES)} RESTART IDENTITY')

    with engine.begin() as conn:
        print("将清空的表：")
        total = 0
        for t in TRUNCATE_TABLES:
            n = conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()
            total += n
            print(f"  {t}: {n} 行")
        print(f"合计 {total} 行")

        if not args.yes:
            print("\n(dry-run；加 --yes 执行 TRUNCATE)")
            return 0
        conn.execute(stmt)
        print("\nTRUNCATE 完成（RESTART IDENTITY）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 测试 + dry-run 冒烟（对 dev 库只读操作安全）**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_vector_tools.py -v && D:/miniConda/envs/rag/python.exe tools/migrate_vector_dimension.py && D:/miniConda/envs/rag/python.exe tools/reset_rag_corpus.py`
Expected: 单测 PASS；dry-run 打印三表当前 `vector(4096)` 与行数（1381/0/3 量级）、reset 打印各表行数，均不改动数据

- [ ] **Step 6: Commit**

```bash
git add tools/migrate_vector_dimension.py tools/reset_rag_corpus.py tests/unit/test_vector_tools.py
git commit -m "feat(tools): vector dimension migration + corpus reset scripts

Separated concerns: migrate only alters empty-table schema (format_type
detection, abort on non-empty); reset only destroys data (explicit table
list, single-statement TRUNCATE, RESTART IDENTITY, mandatory --yes)."
```

---

### Task 5: 摄入侧 question channel 门控

**Files:**
- Modify: `app/ingestion/indexer.py`（约 L458-495）
- Test: `tests/unit/test_question_gating.py`（新建）

背景：`QUESTION_CHANNEL_ENABLED` 目前只控制检索侧（pgvector_store.hybrid_search）；摄入侧 question embed+upsert 无条件执行。必须让 flag 同时门控两侧，`chunk_questions==0` 硬断言才成立。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_question_gating.py`：

```python
"""摄入侧 question 门控：flag off 时不得生成问题向量、不得写 chunk_questions。"""

from app.config import settings
from app.ingestion.chunker import Chunk
from app.ingestion.indexer import DocumentIndexer


def _fake_stack(monkeypatch):
    """离线桩：parser/cleaner 走真实现(.md 直读)，metadata/embedding/rerank 全假。"""
    from app.ingestion import indexer as idx_mod
    from app.llm.embedding import sf_embedding

    calls = {"questions_embedded": 0}

    def fake_generate(chunks):
        for c in chunks:
            c.title = "t"
            c.summary = "s"
            c.questions = ["问题一？"]
        return chunks

    async def fake_embed(texts, *a, **kw):
        return [[0.1] * settings.embedding_dimension for _ in texts]

    orig_embed = sf_embedding.embed_with_fallback

    def counting_embed(texts, *a, **kw):
        # 由 indexer 以 keyword 方式调用；这里只计数
        import asyncio

        results = orig_embed.__wrapped__(texts) if hasattr(orig_embed, "__wrapped__") else None
        return fake_embed(texts)

    monkeypatch.setattr(idx_mod.chunk_metadata_generator, "generate", fake_generate)
    monkeypatch.setattr(
        sf_embedding, "embed_with_fallback", lambda texts, **kw: _run(fake_embed(texts))
    )
    return calls


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_ingest_skips_questions_when_channel_off(monkeypatch):
    """channel off：question_source 不应触发 embed/upsert。"""
    import asyncio

    from app.ingestion import indexer as idx_mod
    from app.llm.embedding import sf_embedding

    upsert_calls = []

    monkeypatch.setattr(settings, "question_channel_enabled", False)

    def fake_generate(chunks):
        for c in chunks:
            c.title = "t"
            c.summary = "s"
            c.questions = ["问题一？"]
        return chunks

    async def fake_embed(texts, *a, **kw):
        return [[0.0] * settings.embedding_dimension for _ in texts]

    def fake_upsert(data):
        upsert_calls.append(data)

    monkeypatch.setattr(idx_mod.chunk_metadata_generator, "generate", fake_generate)
    monkeypatch.setattr(sf_embedding, "embed_with_fallback", lambda texts, **kw: asyncio.run(fake_embed(texts)))
    monkeypatch.setattr(idx_mod.pgvector_store, "upsert_chunk_questions", fake_upsert)

    md = "# 标题\n\n## 小节\n\n这是正文内容，足够长以便切块。\n\n" + ("详细段落内容。" * 50)
    result = DocumentIndexer().index(filename="gating-off.md", content=md.encode("utf-8"), kb_id="kbx")

    assert result["status"] == "indexed"
    assert upsert_calls == []  # 未写任何问题向量


def test_ingest_stores_questions_when_channel_on(monkeypatch):
    import asyncio

    from app.ingestion import indexer as idx_mod
    from app.llm.embedding import sf_embedding

    upsert_calls = []
    monkeypatch.setattr(settings, "question_channel_enabled", True)

    def fake_generate(chunks):
        for c in chunks:
            c.title = "t"
            c.summary = "s"
            c.questions = ["问题一？"]
        return chunks

    async def fake_embed(texts, *a, **kw):
        return [[0.0] * settings.embedding_dimension for _ in texts]

    monkeypatch.setattr(idx_mod.chunk_metadata_generator, "generate", fake_generate)
    monkeypatch.setattr(sf_embedding, "embed_with_fallback", lambda texts, **kw: asyncio.run(fake_embed(texts)))
    monkeypatch.setattr(
        idx_mod.pgvector_store, "upsert_chunk_questions", lambda d: upsert_calls.append(d)
    )

    md = "# 标题\n\n## 小节\n\n这是正文内容，足够长以便切块。\n\n" + ("详细段落内容。" * 50)
    result = DocumentIndexer().index(filename="gating-on.md", content=md.encode("utf-8"), kb_id="kbx")

    assert result["status"] == "indexed"
    assert len(upsert_calls) >= 1  # 问题向量正常落库
```

（注：这两个测试会真实写 dev 库（DocumentIndexer 直接落库）。为避免污染，在文件顶部加 fixture 把 `app.store.db.engine` 指到 ragent_test？——不行，unit 层不碰 DB。改法：monkeypatch `idx_mod.pgvector_store.add_chunks` / `.replace_chunks` / `.delete_orphan_chunk_questions` / `idx_mod._save_document`（实例方法 monkeypatch 到 no-op）以及 `idx_mod._save_chunk_diag`，使全程零落库。实现时按此补齐 stub，断言只看 upsert_calls。）

按上述括号内指引补 stub 后：

- [ ] **Step 2: 运行确认失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_question_gating.py -v`
Expected: 第一个测试 FAIL —— off 状态下 upsert_calls 非空（当前无条件入库）

- [ ] **Step 3: 实现**

`app/ingestion/indexer.py` 中 `if question_data:` 整块（L472-489）外包一层：

```python
            # Embed and store chunk questions for multi-channel retrieval.
            # 摄入侧与检索侧共用同一开关：flag off 时既不生成也不落库，
            # 否则 chunk_questions==0 的完整性硬断言永远无法成立（spec §1.4）。
            if settings.question_channel_enabled:
                question_data = []
                for cd_id, qs in question_source:
                    for pos, q in enumerate(qs):
                        if q.strip():
                            question_data.append(
                                {
                                    "chunk_id": cd_id,
                                    "question": q,
                                    "position": pos,
                                }
                            )
                if question_data:
                    q_texts = [q["question"] for q in question_data]
                    q_emb_results = asyncio.run(sf_embedding.embed_with_fallback(q_texts))
                    valid_q = []
                    for qd, (emb, err) in zip(question_data, q_emb_results):
                        if emb is not None:
                            qd["embedding"] = emb
                            valid_q.append(qd)
                    fail_count = len(question_data) - len(valid_q)
                    if fail_count:
                        logger.warning(
                            "ingest.questions_partial total=%d ok=%d fail=%d",
                            len(question_data),
                            len(valid_q),
                            fail_count,
                        )
                    if valid_q:
                        pgvector_store.upsert_chunk_questions(valid_q)
                        logger.info(
                            "ingest.questions_stored chunk=%d questions=%d ok=%d",
                            len(chunks),
                            len(question_data),
                            len(valid_q),
                        )
```

（即原逻辑原样缩进进 `if settings.question_channel_enabled:`；`delete_orphan_chunk_questions` 保留在外——它只删孤儿，off 时也应跑。原 L490-495 的 logger.info 并入块内如上。）

- [ ] **Step 4: 运行确认通过**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_question_gating.py -v && D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: 新测试 PASS；全量仍 `546 passed / 6 failed / 13 skipped`

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/indexer.py tests/unit/test_question_gating.py
git commit -m "fix(ingest): gate question embed+storage behind QUESTION_CHANNEL_ENABLED

The flag previously gated only the retrieval side; ingest unconditionally
generated and stored question vectors, making the 'chunk_questions==0'
integrity assertion unreachable. Both sides now share one switch."
```

---

### Task 6: baseline_eval.py — 15 题 verified 快测 runner

**Files:**
- Create: `eval/baseline_eval.py`

设计约束（LOCKED evaluator v1）：judge prompt 与评分函数**从 `eval_sany.py` import**（`JUDGE_PROMPT`、`judge_answer`），gold 经 `eval/gold.py` 读取——单一入口，杜绝口径漂移。

- [ ] **Step 1: 创建脚本**

创建 `eval/baseline_eval.py`：

```python
"""Baseline 15 题 verified 快测 —— regression gate，不是最终统计。

纪律（spec §9）：
- gold 一律经 eval.gold（verified_only=True，15 题集合固定）
- judge 复用 eval_sany.JUDGE_PROMPT + judge_answer（locked evaluator v1）
- 每次运行 --name 指定 baseline 名，产物隔离在 baselines/<name>/ 下

用法（后端须已在对应配置下运行）：
    D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-1r
    D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-2 --compare baseline-1r
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR.parent))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from gold import iter_questions  # noqa: E402  # eval.gold —— gold 唯一入口
from eval_sany import BASE_URL, RESULT_PATH, judge_answer  # noqa: E402

load_dotenv(EVAL_DIR / ".env")
load_dotenv(EVAL_DIR.parent / ".env")

OUT_ROOT = EVAL_DIR / "sany_annual_reports" / "baselines"


def login() -> str:
    resp = requests.post(
        f"{BASE_URL}/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def call_rag(query: str, token: str, kb_id: str) -> dict:
    """与 eval_sany.call_rag 相同的 SSE 解析（独立实现以免拉入全局状态）。"""
    body = {"query": query, "knowledge_base_ids": [kb_id]}
    resp = requests.post(
        f"{BASE_URL}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()
    answer_parts, sources, conv_id, error, event_type = [], [], None, None, None
    for line in resp.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            data = line[6:].strip()
            if event_type == "token":
                answer_parts.append(data)
            elif event_type == "sources":
                try:
                    sources = json.loads(data)
                except json.JSONDecodeError:
                    pass
            elif event_type == "metadata":
                try:
                    conv_id = json.loads(data).get("conversation_id", conv_id)
                except json.JSONDecodeError:
                    pass
            elif event_type == "error":
                error = data
            elif event_type == "done":
                break
        event_type = event_type if line.startswith("event: ") else None
    return {"answer": "".join(answer_parts), "sources": sources, "conversation_id": conv_id, "error": error}


def _to_judge_dict(gq) -> dict:
    """GoldQuestion → eval_sany.judge_answer 所需的中文字段 dict。"""
    return {
        "问题": gq.question,
        "类别": gq.category,
        "难度": gq.difficulty,
        "参考答案": gq.gold_answer,
        "答案依据": gq.evidence_basis,
        "考察的RAG易错点": gq.pitfall,
        "常见错误答案": gq.common_wrong_answers,
    }


def summarize(records: list[dict]) -> dict:
    by_cat = defaultdict(list)
    for r in records:
        by_cat[r["category_prefix"]].append(r["judge_score"])
    cats = {
        p: {
            "n": len(v),
            "mean": round(sum(x for x in v if x is not None) / max(len(v), 1), 3),
            "acc_ge2": round(sum(1 for x in v if x is not None and x >= 2) / len(v), 3),
        }
        for p, v in sorted(by_cat.items())
    }
    scored = [r["judge_score"] for r in records if r["judge_score"] is not None]
    return {
        "n": len(records),
        "mean_score_pct": round(100 * sum(scored) / (3 * len(records)), 1) if scored else None,
        "acc_ge2_pct": round(100 * sum(1 for s in scored if s >= 2) / len(records), 1)
        if records
        else None,
        "by_category": cats,
        "errors": [r["question_id"] for r in records if r["error"]],
        "timestamp": datetime.now(UTC).isoformat(),
    }


def compare(summary_a: dict, summary_b: dict, name_a: str, name_b: str) -> str:
    lines = [f"| 类别 | {name_a} acc | {name_b} acc | Δ |", "|---|---|---|---|"]
    for p in sorted(set(summary_a["by_category"]) | set(summary_b["by_category"])):
        a = summary_a["by_category"].get(p, {}).get("acc_ge2")
        b = summary_b["by_category"].get(p, {}).get("acc_ge2")
        d = (b - a) if (a is not None and b is not None) else None
        lines.append(f"| {p} | {a} | {b} | {'' if d is None else f'{d:+.1%}'.rstrip('%')} |".replace("%%", "%"))
    ma, mb = summary_a["mean_score_pct"], summary_b["mean_score_pct"]
    lines.append(f"\noverall mean: {ma} → {mb}")
    aa, ab = summary_a["acc_ge2_pct"], summary_b["acc_ge2_pct"]
    lines.append(f"overall acc(≥2): {aa} → {ab}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="baseline 名，如 baseline-1r / baseline-2")
    parser.add_argument("--compare", default=None, help="对比的既有 baseline 名（打印 delta 表）")
    args = parser.parse_args()

    out_dir = OUT_ROOT / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = iter_questions(verified_only=True)
    assert len(questions) == 15, f"verified 子集应为 15 题，实际 {len(questions)}"

    token = login()
    kbs = requests.get(f"{BASE_URL}/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()
    kb_id = next((kb["id"] for kb in kbs if "三一重工" in kb["name"]), None)
    if not kb_id:
        print("ERROR: 找不到三一重工知识库")
        return 1

    import os

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    base_url = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    judge_model = os.environ.get("JUDGE_MODEL", os.environ.get("CHAT_MODEL", "deepseek-ai/DeepSeek-V3"))

    records = []
    for i, gq in enumerate(questions, 1):
        print(f"[{i}/15] {gq.id} ({gq.category_prefix}) {gq.question[:36]}...", end=" ", flush=True)
        rag = call_rag(gq.question, token, kb_id)
        answer = rag["answer"]
        if not answer.strip() or rag.get("error"):
            rec = {
                "question_id": gq.id,
                "category_prefix": gq.category_prefix,
                "judge_score": None,
                "judge_reason": f"RAG failed: {rag.get('error') or 'empty'}",
                "rag_answer": "",
                "error": rag.get("error") or "empty",
            }
            print("❌ RAG failed")
        else:
            time.sleep(3)
            jd = judge_answer(_to_judge_dict(gq), answer, api_key, base_url, judge_model)
            rec = {
                "question_id": gq.id,
                "category_prefix": gq.category_prefix,
                "judge_score": jd["score"],
                "judge_reason": jd["reason"],
                "rag_answer": answer,
                "sources_count": len(rag.get("sources", [])),
                "error": None,
            }
            icon = {3: "✅", 2: "🔵", 1: "🟡", 0: "❌"}.get(jd["score"], "⚪")
            print(f"{icon} {jd['score']}: {(jd['reason'] or '')[:38]}")
        records.append(rec)
        (out_dir / f"{rec['question_id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        time.sleep(3)

    summary = summarize(records)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== {args.name} ===")
    print(f"overall acc(≥2): {summary['acc_ge2_pct']}%   mean: {summary['mean_score_pct']}%")
    for p, c in summary["by_category"].items():
        print(f"  {p}: {c['acc_ge2_pct']}% ({c['n']}题)")

    if args.compare:
        cmp_path = OUT_ROOT / args.compare / "summary.json"
        if cmp_path.exists():
            other = json.loads(cmp_path.read_text(encoding="utf-8"))
            print(f"\n--- Δ vs {args.compare} ---")
            print(compare(other, summary, args.compare, args.name))
        else:
            print(f"WARN: 找不到 {cmp_path}")

    print(f"\n产物: {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

（实现时若 `eval_sany.py` 顶部有 argparse 之外的副作用导致 import 失败，则把 `JUDGE_PROMPT`/`judge_answer` 抽到 `eval/judge_common.py` 再被两者 import——语义不变，仍单一来源。）

- [ ] **Step 2: import 冒烟（不触网）**

Run: `cd d:/PyProject/ragent-py && D:/miniConda/envs/rag/python.exe -c "import sys; sys.path.insert(0,'eval'); import baseline_eval; qs = baseline_eval.iter_questions(verified_only=True); print(len(qs), [q.id for q in qs])"`
Expected: `15 ['Q01', 'Q02', 'Q03', 'Q11', 'Q12', 'Q17', 'Q18', 'Q25', 'Q26', 'Q32', 'Q33', 'Q50', 'Q51', 'Q55', 'Q56']`

- [ ] **Step 3: Commit**

```bash
git add eval/baseline_eval.py
git commit -m "feat(eval): baseline quick-eval runner over 15 verified questions

Locked evaluator v1 semantics via eval_sany judge reuse; gold via eval.gold
(single entry). Per-baseline artifact dirs + cross-baseline delta table."
```

---

### Task 7: Index Integrity Report 工具

**Files:**
- Create: `tools/index_integrity_report.py`

- [ ] **Step 1: 创建脚本**

```python
"""Index Integrity Report —— 每次 re-index 后必跑的硬断言（spec §8）。

哲学：不允许"配置开了但数据没进来"（或反向）的静默错配再次发生。

用法：
    D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py --expect-documents 3 --expect-questions zero
    ... --expect-questions positive   # Step 2C 之后
退出码非 0 = 有断言失败。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.store.db import engine  # noqa: E402


def vec_dim_of_sample(conn, sql: str) -> int | None:
    row = conn.execute(text(sql)).first()
    if not row or row[0] is None:
        return None
    s = row[0]  # pgvector text 形如 '[0.1,0.2,...]'
    return s.count(",") + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-documents", type=int, default=3)
    parser.add_argument("--expect-questions", choices=["zero", "positive"], required=True)
    args = parser.parse_args()

    failures: list[str] = []

    with engine.connect() as conn:
        def check(label: str, actual, ok: bool):
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {label}: {actual}")
            if not ok:
                failures.append(label)

        n_docs = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        check(f"documents == {args.expect_documents}", n_docs, n_docs == args.expect_documents)

        n_chunks = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
        check("chunks > 0", n_chunks, n_chunks > 0)

        n_null_emb = conn.execute(text("SELECT count(*) FROM chunks WHERE embedding IS NULL")).scalar_one()
        check("chunks.embedding NULL == 0", n_null_emb, n_null_emb == 0)

        dim = vec_dim_of_sample(conn, "SELECT embedding::text FROM chunks WHERE embedding IS NOT NULL LIMIT 1")
        check(f"chunk 向量维度 == {settings.embedding_dimension}", dim, dim == settings.embedding_dimension)

        n_total = max(n_chunks, 1)
        n_et_null = conn.execute(text("SELECT count(*) FROM chunks WHERE embedding_text IS NULL OR embedding_text = ''")).scalar_one()
        avg_et = conn.execute(text("SELECT COALESCE(avg(length(embedding_text)), 0)::int FROM chunks")).scalar_one()
        check("embedding_text 空 ratio == 0", f"{n_et_null}/{n_chunks}", n_et_null == 0)
        print(f"[INFO] embedding_text 平均长度: {avg_et}")

        n_st_empty = conn.execute(text("SELECT count(*) FROM chunks WHERE search_text IS NULL OR search_text = ''")).scalar_one()
        check("search_text 空 ratio == 0", f"{n_st_empty}/{n_chunks}", n_st_empty == 0)

        n_tables = conn.execute(text("SELECT count(*) FROM chunks WHERE chunk_type = 'table'")).scalar_one()
        print(f"[INFO] table chunks: {n_tables} ({100 * n_tables / n_total:.1f}%)")

        n_q = conn.execute(text("SELECT count(*) FROM chunk_questions")).scalar_one()
        if args.expect_questions == "zero":
            check("chunk_questions == 0（硬断言）", n_q, n_q == 0)
        else:
            check("chunk_questions > 0（硬断言）", n_q, n_q > 0)
            qdim = vec_dim_of_sample(
                conn, "SELECT embedding::text FROM chunk_questions WHERE embedding IS NOT NULL LIMIT 1"
            )
            check(f"问题向量维度 == {settings.embedding_dimension}", qdim, qdim == settings.embedding_dimension)

    if failures:
        print(f"\nINTEGRITY FAILED: {len(failures)} 项 -> {failures}")
        return 1
    print("\nINTEGRITY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 对当前 dev 库冒烟（预期 FAIL 是正常的——当前 embedding_text 多数行为空）**

Run: `D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py --expect-documents 3 --expect-questions zero; echo "exit=$?"`
Expected: 能跑通并打印各项 PASS/FAIL（exit 非 0 可接受——旧库本来就不满足新断言；此步只验证脚本本身无异常）

- [ ] **Step 3: Commit**

```bash
git add tools/index_integrity_report.py
git commit -m "feat(tools): index integrity report with hard assertions

documents/chunks/null-embedding/dim/embedding_text/search_text/
chunk_questions expectations — kills silent config-data mismatch."
```

---

### Task 8: Phase A 收尾门

- [ ] **Step 1: 全量测试 + ruff + import**

Run: `D:/miniConda/envs/rag/python.exe -m pytest -q && D:/miniConda/envs/rag/python.exe -m ruff check app/ tests/ && D:/miniConda/envs/rag/python.exe -c "import app.main"`
Expected: `546+N passed / 6 failed / 13 skipped`（6 failed 仍是预存集）；ruff 绿；import 成功

- [ ] **Step 2: 确认 dev 库未被污染**

Run: `docker exec ragent-postgres psql -U ragent -d ragent -t -c "SELECT count(*) FROM chunks; SELECT count(*) FROM documents;"`
Expected: 1381 / 3（Phase A 只改代码，未动数据）

---

# Phase B — Baseline-1R replay（破坏性操作之前的旧库重放）

### Task 9: 跑 Baseline-1R

- [ ] **Step 1: 确认后端以旧配置运行**

检查项：`.env` 未改（仍是 `Qwen/Qwen3-VL-Embedding-8B` / 4096 时代配置）；后端进程活着（`curl http://localhost:8000/health`）。没起就 `D:/miniConda/envs/rag/python.exe -m app.main` 起一个。

- [ ] **Step 2: 运行快测（约 5-8 分钟，真实 LLM）**

Run: `D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-1r`
Expected: 15 题逐题打分落盘 `eval/sany_annual_reports/baselines/baseline-1r/`；打印 overall + 分类

- [ ] **Step 3: 锁定文档 + Commit**

创建 `docs/plans/2026-08-23-baseline-1r-replay.md`：记录 15 题 per-question 分数、分类汇总、与历史 V3 10 题 60% 的定性对照（注明 population 不同不可直比——这正是本 replay 存在的原因）。

```bash
git add docs/plans/2026-08-23-baseline-1r-replay.md eval/sany_annual_reports/baselines/baseline-1r/
git commit -m "docs(baseline): Baseline-1R replay — 15 verified x evaluator v1 on old corpus

Same-population reference point for the Step-1 delta. Question channel
effective OFF (empty table). Run BEFORE any destructive operation."
```

---

# Phase C — Step 1：Embedding 配置迁移（→ Baseline-2）

### Task 10: config 切换 + 维度冒烟

- [ ] **Step 1: config 默认值切换**

`app/config.py`：

```python
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dimension: int = 1024
```

同时更新 `current_embedding_version` 注释与默认值（本轮全量重插后的 corpus 代际标签，hybrid_search 过滤用）：

```python
    # 2 = 第二代 corpus（Qwen3-Embedding-8B@1024，裸 text 输入，Baseline-2）；
    # 3 = 第三代 corpus（表格归一化 retrieval_text 输入，Baseline-3+，见 Step 2A）。
    # hybrid_search 加 AND embedding_version = :v 隔离不同代际。
    current_embedding_version: int = 2  # env: CURRENT_EMBEDDING_VERSION
```

- [ ] **Step 2: .env 更新**

编辑项目根 `.env`（gitignored）：确认/设置 `QUESTION_CHANNEL_ENABLED=false`。（embedding model/dimension 走 config 默认即可；若 `.env` 里曾显式写过旧值须同步改掉。）

- [ ] **Step 3: SiliconFlow 冒烟（不通过后端，直接验证 API 契约）**

Run: `curl -s -X POST https://api.siliconflow.cn/v1/embeddings -H "Authorization: Bearer $SILICONFLOW_API_KEY" -H "Content-Type: application/json" -d '{"model":"Qwen/Qwen3-Embedding-8B","input":"维度冒烟","dimensions":1024}' | head -c 200`
（`$SILICONFLOW_API_KEY` 从 .env 取：`export $(grep SILICONFLOW_API_KEY .env | xargs)` 后再跑。）
Expected: JSON 含 1024 维 embedding 数组。**失败则 STOP**——回 spec §9 备选路线（bge-m3 或维持 4096），找用户决策。

- [ ] **Step 4: 单测确认 dimensions 契约联动**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_embed_fallback.py tests/unit/test_cache.py -q`
Expected: PASS（测试读 settings，自动跟随新维度）

- [ ] **Step 5: Commit（代码面）**

```bash
git add app/config.py
git commit -m "feat(config): switch embedding config to Qwen3-Embedding-8B @1024

Configuration-migration step (model+dimension together, spec P0-1).
current_embedding_version -> 2 labels the post-migration corpus
generation. Runtime pair: QUESTION_CHANNEL_ENABLED=false in .env."
```

---

### Task 11: 清库 → 迁维 → 重启

- [ ] **Step 1: 停后端**

杀掉 uvicorn 进程（或其终端 Ctrl-C）。确认：`curl -s http://localhost:8000/health || echo DOWN`

- [ ] **Step 2: 清库（用户已明确授权本次清空：chunks+对话等，保留用户/KB）**

Run: `D:/miniConda/envs/rag/python.exe tools/reset_rag_corpus.py --yes`
Expected: 打印各行数后 TRUNCATE 完成。核对：

Run: `docker exec ragent-postgres psql -U ragent -d ragent -t -c "SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM conversations), (SELECT count(*) FROM users), (SELECT count(*) FROM knowledge_bases);"`
Expected: `0 | 0 | N | 1`（users/KB 保留）

- [ ] **Step 3: 维度迁移**

Run: `D:/miniConda/envs/rag/python.exe tools/migrate_vector_dimension.py --apply`
Expected: 三表 SKIP/ALTER 日志，最终"迁移完成：全部 embedding 列 -> vector(1024)"

- [ ] **Step 4: 重启 + 守口**

Run: `D:/miniConda/envs/rag/python.exe -m app.main &`（后台）→ `curl http://localhost:8000/health` → `D:/miniConda/envs/rag/python.exe -c "import app.main"` → `D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: health OK；测试基线不变（unit 层哨兵凭据不触真实服务；integration 用 ragent_test 库不受影响——若 ragent_test 库里也有 4096 列，integration conftest 的 schema 初始化会用新 config 建 1024，属预期）

- [ ] **Step 5: 记录（无 commit——纯运维步骤）**

在执行日志（后续 baseline-2 文档的"过程"节）记下：清库行数、ALTER 输出、启动时间。

---

### Task 12: 全量重摄三份年报

- [ ] **Step 1: 登录 + 上传（multipart，走完整 ingestion 管线）**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"admin123"}' | D:/miniConda/envs/rag/python.exe -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
KBID=$(curl -s http://localhost:8000/api/v1/kb -H "Authorization: Bearer $TOKEN" | D:/miniConda/envs/rag/python.exe -c "import json,sys;print(next(k['id'] for k in json.load(sys.stdin) if '三一重工' in k['name']))")
for Y in 2023 2024 2025; do
  curl -s -X POST http://localhost:8000/api/v1/documents/upload \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@eval/sany_annual_reports/三一重工_${Y}年年度报告.pdf" \
    -F "kb_id=$KBID"
done
```

Expected: 三个 JSON 响应 `"status":"processing"`

- [ ] **Step 2: 轮询至 indexed（预计 10-30 分钟，embedding 限速 5 RPS）**

```bash
watch -n 30 'curl -s "http://localhost:8000/api/v1/documents?limit=10" -H "Authorization: Bearer '$TOKEN'" | D:/miniConda/envs/rag/python.exe -c "import json,sys;[print(d[\"filename\"], d[\"status\"], d[\"embedded_chunk_count\"], \"/\", d[\"chunk_count\"]) for d in json.load(sys.stdin)]"'
```

Expected: 三份文档最终 `indexed`，embedded==chunk 总数。任何一份 `failed` → 查 `logs/ragent-*.log` 的 `ingest.*`，修复后重新上传该文档。

- [ ] **Step 3: 完整性报告（含 chunk_questions==0 硬断言）**

Run: `D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py --expect-documents 3 --expect-questions zero`
Expected: `INTEGRITY PASSED`。**任何 FAIL 都必须解决后才许进入评测。**

---

### Task 13: Baseline-2 快测 + 锁定

- [ ] **Step 1: 快测**

Run: `D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-2 --compare baseline-1r`
Expected: 产物落盘 + delta 表打印

- [ ] **Step 2: Gate 判定（spec §4.4）**

对照 Baseline-1R：总分不低于 −10pp 且无单一类别系统性崩塌。
- 通过 → 继续
- 不通过 → **STOP**：记录结果到 docs/plans/，执行回滚（config 回退 + migrate --target 4096 + reset + 重摄），向用户汇报分析后再议

- [ ] **Step 3: 锁定文档 + Commit**

创建 `docs/plans/2026-08-23-baseline-2-locked.md`：per-question 明细、分类对比表、Gate 结论、过程记录（Task 11-12）。更新 `docs/plans/README.md` 索引。

```bash
git add docs/plans/2026-08-23-baseline-2-locked.md docs/plans/README.md eval/sany_annual_reports/baselines/baseline-2/
git commit -m "docs(baseline): Baseline-2 LOCKED — Qwen3-Embedding-8B@1024, channel off

Measures the embedding CONFIGURATION swap (model+dim), not dim alone.
Gate: >= Baseline-1R -10pp, no category collapse."
```

---

# Phase D — Step 2A：表格感知摄入（→ Baseline-3）

### Task 14: build_retrieval_text 纯函数（TDD）

**Files:**
- Create: `app/ingestion/retrieval_text.py`
- Test: `tests/unit/test_retrieval_text.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_retrieval_text.py`：

```python
"""表格归一化检索表示 —— 行级自包含自然语言句（spec §5.1）。"""

from app.ingestion.retrieval_text import RetrievalTextResult, build_retrieval_text

BIG_TABLE = """| 项目 | 2023年 | 2024年 |
| --- | --- | --- |
| 营业收入 | 100亿元 | 150亿元 |
| 净利润 | 10亿元 | 15亿元 |
| 归母净资产 | 500亿元 | 520亿元 |
| 经营现金流 | 80亿元 | 95亿元 |
| 研发投入 | 5亿元 | 6亿元 |
| 总资产 | 900亿元 | 950亿元 |"""


def test_plain_text_passthrough_unchanged():
    res = build_retrieval_text("这是普通段落。\n第二段。")
    assert isinstance(res, RetrievalTextResult)
    assert res.text == "这是普通段落。\n第二段。"
    assert res.chunk_type is None
    assert res.tables == []


def test_big_table_expanded_to_selfcontained_rows():
    res = build_retrieval_text(BIG_TABLE)
    assert res.chunk_type == "table"
    assert len(res.tables) == 1
    assert res.tables[0]["headers"] == ["项目", "2023年", "2024年"]
    assert res.tables[0]["data_rows"] == 6
    # 每行必须带行首实体（第一列），脱离表头仍可独立匹配
    assert "营业收入：2023年为100亿元，2024年为150亿元。" in res.text
    assert "净利润：2023年为10亿元，2024年为15亿元。" in res.text
    # 原始管道行不得残留在检索表示里
    assert "| 营业收入 |" not in res.text
    assert "---" not in res.text


def test_header_line_kept_for_context():
    res = build_retrieval_text(BIG_TABLE)
    first_line = res.text.split("\n")[0]
    assert "项目" in first_line and "2023年" in first_line  # 表头行保留


def test_mixed_paragraph_and_table():
    mixed = "前文说明如下：\n" + BIG_TABLE + "\n后文备注。"
    res = build_retrieval_text(mixed)
    assert res.chunk_type == "table"
    assert res.text.startswith("前文说明如下：")
    assert res.text.endswith("后文备注。")
    assert "营业收入：2023年为100亿元" in res.text


def test_small_nl_style_table_not_double_processed():
    """已被 chunker._clean_table_text 转成自然语言的小表不应再被当 markdown 处理。"""
    nl = "指示灯 绿色 正常\n指示灯 红色 故障"
    res = build_retrieval_text(nl)
    assert res.chunk_type is None
    assert res.text == nl


def test_ragged_row_falls_back_to_space_join():
    ragged = """| 项目 | 2023年 | 2024年 |
| --- | --- | --- |
| 营业收入 | 100亿元 |
| 净利润 | 10亿元 | 15亿元 |
| 归母净资产 | 500亿元 | 520亿元 |
| 经营现金流 | 80亿元 | 95亿元 |
| 研发投入 | 5亿元 | 6亿元 |"""
    res = build_retrieval_text(ragged)
    assert res.chunk_type == "table"
    assert "100亿元" in res.text  # 缺列行不丢数据


def test_section_header_prefix_preserved():
    text = "【2023年 / 主要会计数据】\n" + BIG_TABLE
    res = build_retrieval_text(text)
    assert res.text.startswith("【2023年 / 主要会计数据】")


def test_deterministic_output():
    assert build_retrieval_text(BIG_TABLE).text == build_retrieval_text(BIG_TABLE).text
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_retrieval_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.retrieval_text'`

- [ ] **Step 3: 实现**

创建 `app/ingestion/retrieval_text.py`：

```python
"""build_retrieval_text() — 表格感知的检索表示构造（Issue #2 第一期）。

与 build_embedding_text()（metadata prefix builder）平级、各司其职：
- build_embedding_text：文档/章节前缀增强 —— 保留供 ablation，生产不启用
- build_retrieval_text：表格块行展开归一化 —— 本模块

双表示架构（spec §5.1）：
    chunks.text           原始 Markdown（generation 通道，结构无损）
    chunks.embedding_text 本函数输出（retrieval 通道：embed 输入 + BM25 词源）

行展开格式：每行一条自包含句——「{首列}：{表头2}为{值2}，{表头3}为{值3}。」
行句子带行首实体（年份/科目），脱离表头仍可独立匹配；
不是「2024 | 150亿 | 10%」式的管道拼接。

纯字符串处理，无 LLM、无 IO；可缓存可测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEP_ROW_RE = re.compile(r"^[\s|:\-]+$")


@dataclass
class TableBlock:
    """单个 markdown 表格块的解析结果。"""

    headers: list[str]
    rows: list[list[str]]  # 已剥管道的单元格


@dataclass
class RetrievalTextResult:
    text: str  # 归一化后的完整检索表示
    chunk_type: str | None  # 含表格块时为 "table"，否则 None
    tables: list[dict] = field(default_factory=list)  # [{"headers": [...], "data_rows": n}]


def _split_md_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _find_table_block(lines: list[str], start: int) -> tuple[int, TableBlock] | None:
    """lines[start] 起是否是一个 markdown 表格块；是则返回 (end_idx_exclusive, block)。"""
    if "|" not in lines[start]:
        return None
    if start + 1 >= len(lines) or not _SEP_ROW_RE.match(lines[start + 1]):
        return None
    headers = _split_md_row(lines[start])
    if not headers or not any(headers):
        return None
    rows: list[list[str]] = []
    j = start + 2
    while j < len(lines):
        ln = lines[j]
        if "|" not in ln:
            break
        stripped = ln.strip()
        if not stripped.startswith("|"):
            break
        rows.append(_split_md_row(ln))
        j += 1
    return j, TableBlock(headers=headers, rows=rows)


def _render_table(block: TableBlock) -> tuple[str, dict]:
    """表格 → 「表头行 + 每行自包含句」。返回 (text, meta)。"""
    out = ["| " + " | ".join(block.headers) + " |"]
    for row in block.rows:
        entity = row[0] if row else ""
        if not entity:
            out.append(" ".join(c for c in row if c))
            continue
        pairs = [
            f"{block.headers[k]}为{row[k]}"
            for k in range(1, min(len(row), len(block.headers)))
            if k < len(row) and row[k]
        ]
        out.append(f"{entity}：{'，'.join(pairs)}。" if pairs else entity)
    meta = {"headers": block.headers, "data_rows": len(block.rows)}
    return "\n".join(out), meta


def build_retrieval_text(chunk_text: str) -> RetrievalTextResult:
    """扫描 chunk 文本中的 markdown 表格块并归一化；其余文本原样保留。"""
    if not chunk_text or "|" not in chunk_text:
        return RetrievalTextResult(text=chunk_text or "", chunk_type=None, tables=[])

    lines = chunk_text.split("\n")
    out_lines: list[str] = []
    tables: list[dict] = []
    i = 0
    while i < len(lines):
        found = _find_table_block(lines, i)
        if found is None:
            out_lines.append(lines[i])
            i += 1
            continue
        end, block = found
        rendered, meta = _render_table(block)
        out_lines.append(rendered)
        tables.append(meta)
        i = end
    return RetrievalTextResult(
        text="\n".join(out_lines),
        chunk_type="table" if tables else None,
        tables=tables,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_retrieval_text.py -v`
Expected: 8 个测试全 PASS（若个别断言与实现对不上，修实现不改测试语义——测试即 spec）

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/retrieval_text.py tests/unit/test_retrieval_text.py
git commit -m "feat(ingest): build_retrieval_text — table row normalization

Dual representation (Issue #2 #1/#3 phase 1): raw markdown stays in
chunks.text for generation; each data row becomes a self-contained
sentence ('{entity}: {header}为{value}') for the retrieval channel.
Pure function, no LLM."
```

---

### Task 15: indexer 接线（embed 输入 / search_text / 元数据列）

**Files:**
- Modify: `app/ingestion/indexer.py`
- Modify: `app/config.py`（EMBEDDING_INPUT_VERSION bump 说明 + current_embedding_version → 3）
- Test: `tests/integration/test_table_aware_ingestion.py`（新建）

- [ ] **Step 1: config 代际推进**

`app/core/cache.py`：`EMBEDDING_INPUT_VERSION = 1` → `2`（注释同步：2 = 表格归一化 retrieval_text 表示）。
`app/config.py`：`current_embedding_version` 默认 `2` → `3`，注释改为 `3 = 第三代 corpus（表格归一化 retrieval_text 输入，Baseline-3+）`。

- [ ] **Step 2: 写集成测试（fake 层，ragent_test 库）**

创建 `tests/integration/test_table_aware_ingestion.py`：

```python
"""表格感知摄入接线：embed 输入=归一化文本、元数据列落库、search_text 同源。

跑在 ragent_test 库 + fake_llm_stack（离线确定性），不触 dev 库 / 真 LLM。
"""

BIG_TABLE_MD = """# 财务摘要

## 主要会计数据

| 项目 | 2023年 | 2024年 |
| --- | --- | --- |
| 营业收入 | 100亿元 | 150亿元 |
| 净利润 | 10亿元 | 15亿元 |
| 归母净资产 | 500亿元 | 520亿元 |
| 经营现金流 | 80亿元 | 95亿元 |
| 研发投入 | 5亿元 | 6亿元 |
| 总资产 | 900亿元 | 950亿元 |

以上数据摘自年度报告。"""


def test_table_chunk_dual_representation(integration_db, fake_llm_stack, monkeypatch):
    from app.config import settings
    from app.ingestion.indexer import DocumentIndexer
    from app.store.pgvector_store import get_chunks_by_document

    monkeypatch.setattr(settings, "question_channel_enabled", False)

    result = DocumentIndexer().index(
        filename="table-doc.md",
        content=BIG_TABLE_MD.encode("utf-8"),
        kb_id="kb-test",
    )
    assert result["status"] == "indexed", result
    chunks = get_chunks_by_document(result["document_id"])
    assert chunks, "应有 chunk 落库"

    table_chunks = [c for c in chunks if c.get("chunk_type") == "table"]
    assert table_chunks, "至少一个 chunk 标记为 table"

    tc = table_chunks[0]
    # 双表示：text 保原始 markdown，embedding_text 是归一化文本且二者不同
    assert "| 营业收入 |" in tc["text"]
    assert "营业收入：2023年为100亿元" in tc["embedding_text"]
    assert tc["embedding_text"] != tc["text"]
    # 元数据列
    assert tc["table_headers"] == ["项目", "2023年", "2024年"]
    assert (tc["table_meta"] or [{}])[0]["data_rows"] == 6
    # embedding_version 跟随 config 代际
    assert tc["embedding_version"] == settings.current_embedding_version
    # BM25 词源来自归一化文本（分词后包含实体词）
    assert "100亿" in (tc["search_text"] or "") or "营业收入" in (tc["search_text"] or "")


def test_plain_chunk_embedding_text_equals_text(integration_db, fake_llm_stack, monkeypatch):
    """纯文本 chunk 的 retrieval 表示 == 原文（维持现状，不加前缀）。"""
    from app.config import settings
    from app.ingestion.indexer import DocumentIndexer
    from app.store.pgvector_store import get_chunks_by_document

    monkeypatch.setattr(settings, "question_channel_enabled", False)

    md = "# 文档\n\n## 章节\n\n" + ("这是一段没有表格的正文。" * 40)
    result = DocumentIndexer().index(
        filename="plain-doc.md", content=md.encode("utf-8"), kb_id="kb-test"
    )
    chunks = get_chunks_by_document(result["document_id"])
    assert all(c.get("chunk_type") is None for c in chunks)
```

（注：`integration_db` / `fake_llm_stack` fixture 名称以 `tests/integration/conftest.py` 实际定义为准——打开确认，若叫别的名字（如 `test_db`）就用实际名。`fake_llm_stack` 需额外 patch metadata 生成器让 questions 生成（现有 fixture 已含）；`get_chunks_by_document` 返回 dict 的键名若缺 `chunk_type/table_headers/table_meta`，需在 `pgvector_store.get_chunks_by_document` 的投影 dict 中补这三个键。）

- [ ] **Step 3: 运行确认失败**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_table_aware_ingestion.py -v`
Expected: FAIL —— `chunk_type` 为 None / `embedding_text == text`（接线未做）

- [ ] **Step 4: 接线实现**

`app/ingestion/indexer.py`：

1. 顶部 import：`from app.ingestion.retrieval_text import build_retrieval_text`

2. embed 输入切换——L298 附近：

```python
            # 检索通道输入：表格 chunk 用归一化文本，其余原文（spec §5.1 双表示）
            _rt_map = {id(c): build_retrieval_text(c.text) for c in new_chunks}
            _embed_inputs = [_rt_map[id(c)].text for c in new_chunks]
```

（`_embed_fut` 提交行保持使用 `_embed_inputs`。）

3. 主循环中（L312 起）每个 chunk 计算 `rt = _rt_map[id(c)] if not is_reused else None`；`chunks_data.append({...})` 内：

```python
                    "embedding_text": rt.text if rt else c.text,
                    "embedding_version": settings.current_embedding_version,
                    "chunk_type": rt.chunk_type if rt else None,
                    "table_headers": rt.tables[0]["headers"] if rt and rt.tables else None,
                    "table_meta": rt.tables if rt and rt.tables else None,
```

并把 `search_text` 两处赋值的 `tokenize(c.text, ...)` → `tokenize(rt.text if rt else c.text, stopwords=True)`（is_reused 分支的兜底 tokenize 保持对旧 search_text 的回退语义，仅新块换源）。

4. `get_chunks_by_document`（pgvector_store.py L67+）投影 dict 补：

```python
                "embedding_text": r.embedding_text,
                "chunk_type": r.chunk_type,
                "table_headers": r.table_headers,
                "table_meta": r.table_meta,
```

- [ ] **Step 5: 运行确认通过 + 全量守口**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_table_aware_ingestion.py -v && D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: 新集成测试 PASS；全量基线不变

- [ ] **Step 6: ruff + Commit**

Run: `D:/miniConda/envs/rag/python.exe -m ruff check app/ tests/ && D:/miniConda/envs/rag/python.exe -m ruff format app/ingestion/retrieval_text.py app/ingestion/indexer.py`

```bash
git add app/ingestion/indexer.py app/ingestion/retrieval_text.py app/config.py app/core/cache.py app/store/pgvector_store.py tests/integration/test_table_aware_ingestion.py
git commit -m "feat(ingest): wire table-aware retrieval text into indexing

Embed input / embedding_text column / BM25 search_text all derive from
build_retrieval_text (tables normalized, plain text unchanged). Metadata
columns persisted on both store paths; get_chunks_by_document exposes
them. Corpus generation -> embedding_version 3, cache input_version 2.
Mechanism reused per spec; prior v2-prefix conclusion NOT carried over."
```

---

### Task 16: 重摄 + Baseline-3

- [ ] **Step 1: 重启后端**

`.env` 保持 `QUESTION_CHANNEL_ENABLED=false`。重启 `D:/miniConda/envs/rag/python.exe -m app.main`。

- [ ] **Step 2: 删除旧文档 → 重新上传（绕 hash 复用强制全新摄入）**

用 Task 12 的 TOKEN/KBID 流程：先 `GET /api/v1/documents` 拿 3 个 document_id，逐个 `DELETE /api/v1/documents/{id}`；再上传三份 PDF；轮询至 indexed。

- [ ] **Step 3: 完整性报告**

Run: `D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py --expect-documents 3 --expect-questions zero`
Expected: PASSED；`table chunks` 占比 INFO 行应有非零数字（年报表格密集，预期 >5%）

- [ ] **Step 4: 快测 + Gate（spec §5.3：B 类不得下降为 Gate，改善为 Target）**

Run: `D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-3 --compare baseline-2`
Expected: B 类 acc 相对 baseline-2 不降；其余类别合计不低于 −10pp

不通过 → STOP，记录 + 分析（重点看非表格类是否被行展开噪声误伤），向用户汇报。

- [ ] **Step 5: 锁定文档 + Commit**

创建 `docs/plans/2026-08-23-baseline-3-locked.md`（含 table chunk 占比、B 类明细、C 类观察项记录），更新 README 索引。

```bash
git add docs/plans/2026-08-23-baseline-3-locked.md docs/plans/README.md eval/sany_annual_reports/baselines/baseline-3/
git commit -m "docs(baseline): Baseline-3 LOCKED — table-aware ingestion, channel off

Single variable vs Baseline-2: table dual-representation + metadata.
B-class gate held (must-not-regress), target improvement recorded."
```

---

# Phase E — Step 2C：Question channel 激活（→ Baseline-4）

### Task 17: 开闸重摄

- [ ] **Step 1: .env 切换 + 重启**

`.env`：`QUESTION_CHANNEL_ENABLED=true`。重启后端。

- [ ] **Step 2: 删除重传三份文档（同 Task 16 流程）**

轮询至 indexed。注意本轮摄入会多跑 question embed（每 chunk 4-5 问），耗时比上轮长。

- [ ] **Step 3: 完整性报告**

Run: `D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py --expect-documents 3 --expect-questions positive`
Expected: `chunk_questions > 0` 硬断言 PASS + 问题向量维度 PASS

---

### Task 18: Baseline-4 快测 + 锁定

- [ ] **Step 1: 快测**

Run: `D:/miniConda/envs/rag/python.exe eval/baseline_eval.py --name baseline-4 --compare baseline-3`
Gate: 总分不低于 Baseline-3 −10pp、无类别崩塌（RRF 权重等调优属后续实验，本期只用默认 0.15）。

- [ ] **Step 2: 锁定文档 + Commit**

创建 `docs/plans/2026-08-23-baseline-4-locked.md`（记录 question channel 首次真实激活的效果——历史上它从未有过数据），更新 README 索引。

```bash
git add docs/plans/2026-08-23-baseline-4-locked.md docs/plans/README.md eval/sany_annual_reports/baselines/baseline-4/
git commit -m "docs(baseline): Baseline-4 LOCKED — question channel live for the first time

First effective activation in project history (prior corpora predated
the feature). Default RRF weight 0.15, tuning deferred."
```

---

# Phase F — 收官

### Task 19: 65 题正式 benchmark

- [ ] **Step 1: 全量评测（locked evaluator v1，约 10 分钟）**

先把现有 `eval/sany_annual_reports/eval_results.json` 移开备份（`mv eval_results.json eval_results.pre-v2.json`），然后：

Run: `D:/miniConda/envs/rag/python.exe eval/eval_sany.py --no-resume`
Expected: `eval_report.md` + `eval_results.json` 生成

- [ ] **Step 2: 分面统计**

报告必须含 overall 之外的分面（防收益被 overall 稀释）：A-J 十类各自 acc、answerable/refusal 子集、numeric 题子集（B/D 类）、table 题（B 类）、cross-doc（C 类）。可用 `eval/eval_detail.py` 或从 `eval_results.json` 现算，结果并入报告文档。

- [ ] **Step 3: 归档 + Commit**

```bash
mkdir -p eval/sany_annual_reports/baselines/final-65q
cp eval/sany_annual_reports/eval_results.json eval/sany_annual_reports/eval_report.md eval/sany_annual_reports/baselines/final-65q/
git add eval/sany_annual_reports/baselines/final-65q/ docs/plans/2026-08-23-rag-v2-final-report.md
git commit -m "docs(benchmark): 65-question formal run under locked evaluator v1 on Baseline-4 corpus"
```

（报告文档 `docs/plans/2026-08-23-rag-v2-final-report.md` 内容：四级 baseline 谱系图 + 各级 delta + 分面表 + 与历史 78.3% 的定性讨论。）

### Task 20: 会话收尾（CLAUDE.md 纪律）

- [ ] **Step 1: 更新 TODO.md**（勾掉已完成项，登记遗留：表格检索策略二期、大表摘要 Baseline-5、citation runtime check）
- [ ] **Step 2: 写 `docs/plans/2026-08-24-next-steps.md`**（本次总结 + 下一步优先级）
- [ ] **Step 3: 更新 memory**（`MEMORY.md` + 新记忆文件：四级 baseline 谱系、隐藏变量教训、db.py 豁免决定）
- [ ] **Step 4: 最终守口**

Run: `D:/miniConda/envs/rag/python.exe -m pytest -q && D:/miniConda/envs/rag/python.exe -m ruff check app/ tests/`
Expected: 基线不变；ruff 绿

```bash
git add TODO.md docs/plans/2026-08-24-next-steps.md
git commit -m "docs(plan): RAG v2 landed — next steps"
```

---

## 回滚预案（任何一步 Gate 不过时）

| 场景 | 动作 |
| --- | --- |
| Task 10 冒烟失败 | 不动库；评估 bge-m3（`EMBEDDING_SEND_DIMENSIONS=false`）或维持 4096 只做 2A；问用户 |
| Task 13 Gate 不过 | config 回退（model/dimension/version）+ `migrate_vector_dimension.py --target 4096`（需先 reset）+ reset + 重摄旧配置 + 重跑快测确认回到 1R 水位 |
| Task 16 Gate 不过 | 代码回退 Task 15 commit（`git revert`），删除重传（恢复裸 text 摄入）= 回到 Baseline-2 状态 |
| Task 18 Gate 不过 | `.env` 关回 false + 删除重传 = 回到 Baseline-3 状态 |

## Self-Review 记录

1. **Spec 覆盖**：§4.2a→Task 10；§4.2b→Task 2/10；§4.2c→Task 1；§4.2d→Task 5；§4.2e→Task 3；§4.2f→Task 4；§4.2g→Task 4；§4.2h→Task 1/2/3/5 测试；§3 Phase 0→Task 6/9；§5→Task 14-16；§6→Task 17-18；§7 清库→Task 11；§8 完整性→Task 7/12/16/17；§9 评测纪律→Task 13/16/18/19；§10 Non-goals 未混入。✓
2. **占位符**：无 TBD；两处"以实际 fixture 名为准/以能跑为准"是对既有代码的核对指令，非设计缺口。
3. **类型一致性**：`RetrievalTextResult(text/chunk_type/tables)` 在 Task 14 定义、Task 15 消费一致；`parse_vector_type`/`get_vector_dim`（脚本内名 `get_column_type`）一致；`EMBEDDING_INPUT_VERSION` Task 1 定义、Task 15 bump；`current_embedding_version` 语义三代谱系前后一致。✓

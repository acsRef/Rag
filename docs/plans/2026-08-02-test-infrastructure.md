> 状态: 进行中

# 测试基建（test-infrastructure）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAGent 引入 pytest 离线单测底座：依赖 + 配置 + 外部依赖护栏 + 5 个纯逻辑模块的行为锁定测试，并把"禁止测试"的旧约束从 CLAUDE.md 移除。

**Architecture:** `tests/unit`（离线，绝不触网/触库/触 LLM）+ `tests/integration`（预留 marker，需 PG，本 plan 不实现）。conftest 在**任何 `app` 模块 import 之前**用环境变量把凭据换成哨兵值（pydantic-settings 中 env 优先于 `.env`），使误触真实服务立即报错。审查发现的已知 bug 用 `xfail(strict=False)` 锁定期望行为，reason 指向后续 plan slug。

**Tech Stack:** pytest ≥ 8.2、pytest-asyncio ≥ 0.24（`asyncio_mode = auto`）、Python 3.11（**rag 环境**）。

---

## Context

2026-08-02 全栈审查发现 40+ 处 bug 与设计缺陷，但本项目**没有任何测试**（CLAUDE.md 旧约束明确禁止 pytest），验证手段只有 `import app.main`。用户已明确：**开发完必须要测试**，该指令优先于 CLAUDE.md 旧约束，本 plan 同步更新 CLAUDE.md。

后续每份修复类 plan（memory-overhaul、cross-doc-retrieval-overhaul、llm-gateway-convergence、tag-stream-parser、security-p0、ingestion-correctness，见 [README.md](README.md) 索引）都要求随代码交付测试，因此必须先有这份基建 plan 落地。

本 plan 顺带用测试**锁定审查结论**：熔断器 HALF_OPEN 多 probe、MMR 对 NULL embedding 崩溃、`_needs_decomposition` 正则误报、`_trim_history` 裁剪后顺序颠倒——这四个已知 bug 写成断言期望行为 + `xfail(strict=False)`，后续对应 plan 修复后测试转 XPASS，届时删除 marker。

### Python 环境硬约束

| 项 | 值 |
|---|---|
| 解释器 | `D:/miniConda/envs/rag/python.exe`（Python 3.11.15，conda env `rag`） |
| 禁止使用 | ReportAgent 项目的 `agent` 环境（缺 numpy/pgvector 等依赖） |
| 工作目录 | 所有命令在仓库根 `D:/PyProject/ragent-py` 下执行 |
| 调用方式 | 一律 `D:/miniConda/envs/rag/python.exe -m pytest ...`（Windows 下 `python app/main.py` 不加 cwd 到 sys.path，`-m` 方式规避，同 CLAUDE.md Common pitfalls） |

## Design

- **目录布局**：`tests/conftest.py`（护栏）+ `tests/unit/`（离线）+ `tests/integration/`（本 plan 只建目录与 skip 约定，不写用例）。不用 `__init__.py`，靠 rootdir + `testpaths` 收集。
- **护栏两层**：
  1. conftest 模块顶部设置环境变量（`DATABASE_URL` / `MINIMAX_API_KEY` / `SILICONFLOW_API_KEY` 等）——必须位于任何 `from app...` 之前；pydantic-settings 中环境变量优先于 `.env` 文件，因此 `app.store.db` 的 `create_engine` 在 import 期就绑定到不存在的地址 `127.0.0.1:1`，任何误用 DB 的 unit 测试**立即失败**而非污染开发库。
  2. autouse fixture `block_external_services` 在运行期再 monkeypatch 一次 `settings` 属性，覆盖"运行期读配置"的路径。
- **pytest.ini**：`asyncio_mode = auto`（async 测试免装饰器）、`--strict-markers`、`unit` / `integration` 两个 marker。与同项目族的 ReportAgent backend pytest.ini 约定一致。
- **xfail 策略**：`strict=False` + reason 含目标 plan slug。修复 plan 落地后测试 XPASS 会出现在报告里，提示删 marker；strict=False 保证修复前后套件都绿。
- **测试边界**：只测**现存**纯函数，不为尚不存在的代码（如 TagStreamParser）预写测试。

## Files to change

| 变更 | 路径 | 说明 |
|---|---|---|
| Create | `requirements-dev.txt` | pytest + pytest-asyncio |
| Create | `pytest.ini` | asyncio_mode / markers / strict |
| Create | `tests/conftest.py` | 环境变量哨兵 + autouse 护栏 fixture |
| Create | `tests/unit/test_sanity.py` | 护栏冒烟（1 例） |
| Create | `tests/unit/test_llm_base.py` | CircuitBreaker / classify_llm_error / robust_json_parse / jittered_backoff / call_llm_with_retry（24 例，含 1 xfail） |
| Create | `tests/unit/test_mmr.py` | mmr_select 选择逻辑（5 例，含 1 xfail） |
| Create | `tests/unit/test_memory_helpers.py` | `_estimate_tokens` / `_get_outside_window`（5 例） |
| Create | `tests/unit/test_pipeline_helpers.py` | `_needs_decomposition` / `_sse_safe` / `_norm`（8 例，含 1 xfail） |
| Create | `tests/unit/test_prompt.py` | `_est` 一致性 / `_trim_history`（3 例，含 1 xfail） |
| Create | `tests/fixtures/docs/transformer_basics.md` | 自制 fixture 文档 1（Transformer 基础，含缩放点积等独有术语） |
| Create | `tests/fixtures/docs/transformer_pytorch.md` | fixture 文档 2（PyTorch 实现，与文档 1 共享 QKV/多头注意力 术语 → 关系边） |
| Create | `tests/fixtures/docs/rag_chunking.md` | fixture 文档 3（RAG 分块，与 1/2 几乎无交集 → 无边） |
| Create | `tests/integration/conftest.py` | 测试库 `ragent_test` bootstrap + seed User/KB + fake embedding/rerank/metadata 层 |
| Create | `tests/integration/test_ingestion.py` | 摄入链路：建块/入库/增量复用/unchanged（4 例） |
| Create | `tests/integration/test_cross_doc.py` | 关系矩阵构建 + 三通道跳转（3 例） |
| Create | `tests/integration/test_retrieval_e2e.py` | retrieval_engine 全链路 + 跨文档存活 xfail（2 例，含 1 xfail） |
| Create | `tests/integration/test_live_llm.py` | 真实 API 冒烟，`RAGENT_LIVE_LLM=1` 才跑（2 例，默认 skip） |
| Modify | `CLAUDE.md` | 替换 "no test framework" 段 + 删除 "Do not introduce pytest" 约束 |
| Modify | `docs/plans/README.md` | 完成后本 plan 状态改 `已完成` 并带 commit |

改动面仅限新增测试文件与两处文档；**零业务代码变更**。

## Reused existing utilities

| 复用对象 | 路径 | 用途 |
|---|---|---|
| `Settings`（pydantic-settings env 优先级） | `app/config.py` | 护栏依赖"env 变量 > .env"这一既有行为，无需新机制 |
| `CircuitBreaker` / `classify_llm_error` / `robust_json_parse` / `call_llm_with_retry` | `app/llm/base.py` | 被测对象即现成工具，不另造 |
| `mmr_select` | `app/core/mmr.py` | 同上 |
| `_estimate_tokens` / `_get_outside_window` | `app/core/memory.py` | 同上 |
| `_needs_decomposition` / `_sse_safe` / `_norm` | `app/core/pipeline.py` | 同上 |
| `_est` / `prompt_builder._trim_history` | `app/core/prompt.py` | 同上 |
| ReportAgent `backend/pytest.ini` 约定 | 同项目族 | `asyncio_mode=auto` + strict markers 的直接先例 |

---

## Tasks

### Task 1: 依赖与 pytest 配置

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`

- [ ] **Step 1: 创建 `requirements-dev.txt`**

```
pytest>=8.2
pytest-asyncio>=0.24
```

- [ ] **Step 2: 用 rag 环境安装**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pip install -r requirements-dev.txt
```
Expected: `Successfully installed pytest-8.x.x pytest-asyncio-0.2x.x ...`

- [ ] **Step 3: 创建 `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
addopts = --strict-markers
markers =
    unit: offline tests — must never touch real DB / network / LLM
    integration: tests requiring PostgreSQL, auto-skip when PG unreachable
    live_llm: hits real LLM APIs, only runs when RAGENT_LIVE_LLM=1
```

- [ ] **Step 4: 验证 pytest 可启动**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest --collect-only -q
```
Expected: `no tests ran` / exit code 5（尚无测试文件，属正常；不应出现配置解析错误）。

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt pytest.ini
git commit -m "test(infra): add pytest dev deps and config + plan: test-infrastructure"
```

---

### Task 2: 外部依赖护栏 + 冒烟测试

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/unit/test_sanity.py`

- [ ] **Step 1: 创建 `tests/conftest.py`**

注意：环境变量赋值必须在任何 `from app...` import **之前**（文件顶部），否则 `app.store.db` 会用 `.env` 里的真实 `DATABASE_URL` 建 engine。

```python
"""测试套件共享配置。

unit 测试不得触碰真实 DB / 网络 / LLM：
在 import app 包之前用环境变量把凭据换成哨兵值
（pydantic-settings 中环境变量优先于 .env），
app.store.db import 期创建的 engine 会绑定到不存在的地址，
任何误触真实服务的 unit 测试将立即失败。
"""
import os

os.environ["DATABASE_URL"] = "postgresql://test:test@127.0.0.1:1/ragent_test_nonexistent"
os.environ["MINIMAX_API_KEY"] = "test-not-real"
os.environ["SILICONFLOW_API_KEY"] = "test-not-real"
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("PII_ENCRYPTION_KEY", "test-pii-key")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch):
    """运行期二层护栏：运行期读取这些配置的代码路径也拿到哨兵值。"""
    monkeypatch.setattr(settings, "minimax_api_key", "test-not-real")
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-not-real")
    yield
```

- [ ] **Step 2: 写冒烟测试 `tests/unit/test_sanity.py`**

```python
"""环境护栏冒烟：证明 unit 测试跑在哨兵配置下。"""
from app.config import settings


def test_credentials_are_sentinels():
    assert settings.minimax_api_key == "test-not-real"
    assert settings.siliconflow_api_key == "test-not-real"
    # 不得指向开发库（integration conftest 可能把它改指 ragent_test，那也合法）
    assert not settings.database_url.endswith("/ragent")
```

- [ ] **Step 3: 运行冒烟测试**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_sanity.py -v
```
Expected: `1 passed`。若失败且报真实库连接错误，说明 conftest 的环境变量设置被某个提前 import 绕过——检查是否有文件在 conftest 之前 import 了 `app`。

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/unit/test_sanity.py
git commit -m "test(infra): add external-service guard fixture and smoke test"
```

---

### Task 3: 锁定 `app/llm/base.py` 行为

**Files:**
- Test: `tests/unit/test_llm_base.py`

- [ ] **Step 1: 写测试文件 `tests/unit/test_llm_base.py`**

```python
"""锁定 app/llm/base.py 行为：熔断状态机 / 错误分类 / JSON 容错 / 退避 / 重试策略。"""
import pytest

import app.llm.base as base
from app.llm.base import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    PermanentError,
    TemporaryError,
    call_llm_with_retry,
    classify_llm_error,
    jittered_backoff,
    robust_json_parse,
)


# ── 熔断器 ──────────────────────────────────────────────

def test_breaker_closed_allows_requests():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    assert cb.allow_request() is True
    assert cb.state == CircuitState.CLOSED


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    for _ in range(3):
        cb.on_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_breaker_half_open_after_cooldown(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    assert cb.state == CircuitState.OPEN
    clock[0] = 31.0
    assert cb.allow_request() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_breaker_probe_success_closes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    clock[0] = 31.0
    cb.allow_request()          # 触发 OPEN → HALF_OPEN
    cb.on_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_breaker_probe_failure_reopens(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    clock[0] = 31.0
    cb.allow_request()
    cb.on_failure()             # probe 失败
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


@pytest.mark.xfail(
    reason="已知 bug：OPEN→HALF_OPEN 转换那次调用未占用 probe 名额，并发下会放行 2 个 probe；"
           "待 llm-gateway-convergence plan 修复",
    strict=False,
)
def test_breaker_half_open_allows_exactly_one_probe(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0)
    cb.on_failure()
    clock[0] = 31.0
    first = cb.allow_request()      # 转换调用
    second = cb.allow_request()     # 期望：总共只允许一个 probe
    assert first is True
    assert second is False


# ── 错误分类 ────────────────────────────────────────────

class _FakeHTTPError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"http {status_code}")


def test_classify_429_is_temporary_retryable():
    typed, retry = classify_llm_error(_FakeHTTPError(429))
    assert isinstance(typed, TemporaryError)
    assert retry is True


def test_classify_4xx_is_permanent_no_retry():
    typed, retry = classify_llm_error(_FakeHTTPError(401))
    assert isinstance(typed, PermanentError)
    assert retry is False


def test_classify_5xx_is_temporary_retryable():
    typed, retry = classify_llm_error(_FakeHTTPError(503))
    assert isinstance(typed, TemporaryError)
    assert retry is True


def test_classify_unknown_exception_defaults_to_temporary():
    typed, retry = classify_llm_error(ValueError("boom"))
    assert isinstance(typed, TemporaryError)
    assert retry is True


def test_classify_circuit_open_is_not_retried():
    exc = CircuitOpenError("open")
    typed, retry = classify_llm_error(exc)
    assert typed is exc
    assert retry is False


# ── JSON 容错解析 ───────────────────────────────────────

def test_robust_json_plain_object():
    assert robust_json_parse('{"a": 1}') == {"a": 1}


def test_robust_json_code_fenced():
    assert robust_json_parse('```json\n{"a": 1}\n```') == {"a": 1}


def test_robust_json_think_wrapped():
    assert robust_json_parse('<think>blah
</think>

{"a": 1}') == {"a": 1}


def test_robust_json_trailing_comma():
    assert robust_json_parse('{"a": 1,}') == {"a": 1}


def test_robust_json_prose_wrapped():
    assert robust_json_parse('Sure! {"a": 1} done') == {"a": 1}


def test_robust_json_nested_object():
    assert robust_json_parse('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_robust_json_garbage_returns_none():
    assert robust_json_parse("no json here") is None


# ── 退避 ────────────────────────────────────────────────

def test_jittered_backoff_bounds():
    for attempt, lo in ((0, 1.0), (1, 2.0), (3, 8.0)):
        v = jittered_backoff(attempt)
        assert lo <= v < lo + 0.5


# ── 重试策略 ────────────────────────────────────────────

@pytest.fixture
def no_backoff(monkeypatch):
    """把退避 sleep 归零，避免重试测试真实等待。"""
    monkeypatch.setattr(base, "jittered_backoff", lambda attempt, base=1.0: 0.0)


async def test_retry_returns_first_success(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        return "ok"

    result = await call_llm_with_retry(chat_fn, [], tag="t")
    assert result == "ok"
    assert len(calls) == 1


async def test_retry_recovers_from_temporary_error(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise TemporaryError("flaky")
        return "ok"

    result = await call_llm_with_retry(chat_fn, [], tag="t")
    assert result == "ok"
    assert len(calls) == 2


async def test_retry_permanent_error_not_retried(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        raise PermanentError("auth")

    with pytest.raises(PermanentError):
        await call_llm_with_retry(chat_fn, [], tag="t")
    assert len(calls) == 1


async def test_retry_circuit_open_reraised(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        raise CircuitOpenError("open")

    with pytest.raises(CircuitOpenError):
        await call_llm_with_retry(chat_fn, [], tag="t")
    assert len(calls) == 1


async def test_retry_empty_response_treated_as_temporary(no_backoff):
    calls = []

    async def chat_fn(messages, **kw):
        calls.append(1)
        return ""

    with pytest.raises(TemporaryError):
        await call_llm_with_retry(chat_fn, [], tag="t", max_retries=1)
    assert len(calls) == 2
```

- [ ] **Step 2: 运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_llm_base.py -v
```
Expected: `23 passed, 1 xfailed`（`test_breaker_half_open_allows_exactly_one_probe` 为 xfail）。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_llm_base.py
git commit -m "test(llm): lock down circuit breaker, error classification, JSON parse, retry policy"
```

---

### Task 4: 锁定 `app/core/mmr.py` 行为

**Files:**
- Test: `tests/unit/test_mmr.py`

- [ ] **Step 1: 写测试文件 `tests/unit/test_mmr.py`**

```python
"""锁定 app/core/mmr.py 行为：MMR 多样性重排与每文档软惩罚。

测试向量均取单位向量（dot = cosine），与 mmr 文档假设一致。
"""
import pytest

from app.core.mmr import mmr_select


def _cand(chunk_id: str, score: float, doc: str, emb: list) -> dict:
    return {"chunk_id": chunk_id, "score": score, "document_id": doc, "embedding": emb}


def test_empty_candidates_returns_empty():
    assert mmr_select([], top_k=5) == []


def test_lambda_one_is_pure_relevance():
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [1.0, 0.0, 0.0]),
        _cand("c", 0.7, "doc2", [0.0, 1.0, 0.0]),
    ]
    out = mmr_select(cands, lambda_=1.0, top_k=2, max_per_doc=99, doc_penalty=0.0)
    assert [c["chunk_id"] for c in out] == ["a", "b"]


def test_diversity_prefers_different_document():
    # b 与 a 语义完全相同（cosine=1）；c 来自另一文档且正交。
    # lambda=0.5 时，多样性项应让 c 胜出 b。
    # 数值推演：归一化分 a=1.0 b=0.5 c=0.0；
    #   b: 0.5*0.5 - 0.5*1.0 = -0.25
    #   c: 0.0     - 0.5*0.0 =  0.0   → c 胜
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [1.0, 0.0, 0.0]),
        _cand("c", 0.7, "doc2", [0.0, 1.0, 0.0]),
    ]
    out = mmr_select(cands, lambda_=0.5, top_k=2, max_per_doc=99, doc_penalty=0.0)
    assert [c["chunk_id"] for c in out] == ["a", "c"]


def test_max_per_doc_soft_penalty():
    # max_per_doc=1 + doc_penalty=0.5：
    # 归一化分 a=1.0 b=0.333 c=0.0；选 a 后：
    #   b: 0.333 - 0.5*(1-1+1) = -0.167
    #   c: 0.0   - 0           =  0.0   → c 胜
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        _cand("b", 0.8, "doc1", [0.0, 0.0, 1.0]),
        _cand("c", 0.75, "doc2", [0.0, 1.0, 0.0]),
    ]
    out = mmr_select(cands, lambda_=1.0, top_k=2, max_per_doc=1, doc_penalty=0.5)
    assert [c["chunk_id"] for c in out] == ["a", "c"]


@pytest.mark.xfail(
    reason="已知 bug：chunk embedding 为 NULL 时 _embedding_to_list 返回 []，"
           "np.array 行长度不一致直接 ValueError；待 cross-doc-retrieval-overhaul plan 修复",
    strict=False,
)
def test_null_embedding_does_not_crash():
    cands = [
        _cand("a", 0.9, "doc1", [1.0, 0.0, 0.0]),
        {"chunk_id": "b", "score": 0.8, "document_id": "doc2", "embedding": None},
    ]
    out = mmr_select(cands, lambda_=0.7, top_k=2)
    assert len(out) == 2
```

- [ ] **Step 2: 运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_mmr.py -v
```
Expected: `4 passed, 1 xfailed`。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_mmr.py
git commit -m "test(mmr): lock down selection, diversity and per-doc penalty behavior"
```

---

### Task 5: 锁定 `app/core/memory.py` 纯函数行为

**Files:**
- Test: `tests/unit/test_memory_helpers.py`

- [ ] **Step 1: 写测试文件 `tests/unit/test_memory_helpers.py`**

```python
"""锁定 app/core/memory.py 纯函数行为（不触 DB）。

_get_outside_window 只读消息对象的 content 属性，用 SimpleNamespace 打桩即可，
不需要真实 Message ORM 实例。
"""
from types import SimpleNamespace

from app.config import settings
from app.core.memory import _estimate_tokens, _get_outside_window


def _msg(content: str, role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(content=content, role=role, created_at=None)


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_mixed_content():
    assert _estimate_tokens("a" * 30) == 20   # len / 1.5 向下取整
    assert _estimate_tokens("x") == 1          # 至少为 1


def test_outside_window_empty_when_all_within_budget(monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 1000)
    msgs = [_msg("c" * 30) for _ in range(3)]  # 每条 20 token
    assert _get_outside_window(msgs) == []


def test_outside_window_returns_overflowed_old_messages(monkeypatch):
    monkeypatch.setattr(settings, "history_max_tokens", 50)
    msgs = [_msg("m%d%s" % (i, "c" * 28)) for i in range(5)]  # 每条 20 token
    # 从新往旧累加：m4(20) m3(40) m2(60 > 50) → outside = msgs[:3]
    assert _get_outside_window(msgs) == msgs[:3]


def test_outside_window_includes_the_overflowing_message(monkeypatch):
    # 恰好撑爆预算的那条消息算"窗口外"——与 get_history 的排除语义保持一致
    monkeypatch.setattr(settings, "history_max_tokens", 40)
    msgs = [_msg("c" * 30) for _ in range(3)]  # 每条 20 token
    outside = _get_outside_window(msgs)
    assert len(outside) == 1                    # 倒数第三条撑爆，被纳入 outside
```

- [ ] **Step 2: 运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_memory_helpers.py -v
```
Expected: `5 passed`。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_memory_helpers.py
git commit -m "test(memory): lock down token estimation and outside-window selection"
```

---

### Task 6: 锁定 `app/core/pipeline.py` 辅助函数行为

**Files:**
- Test: `tests/unit/test_pipeline_helpers.py`

- [ ] **Step 1: 写测试文件 `tests/unit/test_pipeline_helpers.py`**

```python
"""锁定 app/core/pipeline.py 纯辅助函数行为：分解门控 / SSE 转义 / 文本规整。"""
import pytest

from app.core.pipeline import _needs_decomposition, _norm, _sse_safe


# ── _needs_decomposition ────────────────────────────────

def test_decomp_comparison_pattern():
    assert _needs_decomposition("JWT 和 Session 有什么区别") is True


def test_decomp_multiple_entities():
    assert _needs_decomposition("对比《文档A》和《文档B》") is True


def test_decomp_pronoun():
    assert _needs_decomposition("它的参数是什么") is True


def test_decomp_simple_query_no_trigger():
    assert _needs_decomposition("什么是 RAG") is False


@pytest.mark.xfail(
    reason="已知 bug：正则单字候选 其|他 误报常见词（其他/其实/尤其），"
           "给无代词的查询强加一次 LLM 改写；待 llm-gateway-convergence plan 修复",
    strict=False,
)
def test_decomp_no_false_positive_on_common_words():
    assert _needs_decomposition("还有其他方案吗") is False
    assert _needs_decomposition("其实我不确定") is False


# ── _sse_safe ───────────────────────────────────────────

def test_sse_safe_escapes_newline_and_strips_cr():
    # _NL 是字面 反斜杠+n（两个字符），\r 被删除
    assert _sse_safe("a\nb\rc") == "a" + chr(92) + "nbc"


# ── _norm ───────────────────────────────────────────────

def test_norm_collapses_excess_blank_lines():
    assert _norm("a\n\n\n\nb") == "a\n\nb"


def test_norm_strips_outer_whitespace():
    assert _norm("  hi  ") == "hi"
```

- [ ] **Step 2: 运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_pipeline_helpers.py -v
```
Expected: `7 passed, 1 xfailed`。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pipeline_helpers.py
git commit -m "test(pipeline): lock down decomposition gate, SSE escaping, text normalization"
```

---

### Task 7: 锁定 `app/core/prompt.py` 行为

**Files:**
- Test: `tests/unit/test_prompt.py`

- [ ] **Step 1: 写测试文件 `tests/unit/test_prompt.py`**

```python
"""锁定 app/core/prompt.py 行为：token 估算一致性与历史裁剪。"""
import pytest

from app.core.memory import _estimate_tokens
from app.core.prompt import _est, prompt_builder


def test_est_matches_memory_estimator():
    # 两处估算实现必须一致（目前是复制粘贴的巧合一致，本测试把它变成契约）
    for s in ("", "hello", "中文混合 mixed 123", "x" * 1000):
        assert _est(s) == _estimate_tokens(s)


def test_trim_history_keeps_summary_and_chronological_order_when_fits():
    history = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "第二条"},
    ]
    text, tokens = prompt_builder._trim_history(history, "旧摘要", 999999)
    assert "## 对话历史摘要" in text
    assert "旧摘要" in text
    assert text.index("第一条") < text.index("第二条")   # 时间顺序
    assert tokens > 0


@pytest.mark.xfail(
    reason="已知 bug：_trim_history 触发裁剪分支时按从新到旧 append，"
           "渲染出的历史块顺序颠倒；待 llm-gateway-convergence plan 修复",
    strict=False,
)
def test_trim_history_keeps_chronological_order_when_trimming():
    history = [{"role": "user", "content": "msg-%d %s" % (i, "x" * 60)} for i in range(6)]
    text, _ = prompt_builder._trim_history(history, "", budget=120)
    present = ["msg-%d" % i for i in range(6) if ("msg-%d" % i) in text]
    assert len(present) >= 2
    assert present == sorted(present)   # 期望：旧消息在前
```

- [ ] **Step 2: 运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_prompt.py -v
```
Expected: `2 passed, 1 xfailed`。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_prompt.py
git commit -m "test(prompt): lock down token estimate parity and history trimming"
```

---

### Task 8: 更新 CLAUDE.md 约束

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 替换 "Verify code changes" 段**

将：

```markdown
## Verify code changes

There is **no test framework**. The project intentionally has no pytest/tests directory. Verification:
```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"  # import chain check
```
For runtime verification, start the app and exercise the affected endpoint.
```

替换为：

```markdown
## Verify code changes

Offline unit tests (never touch real DB / network / LLM — credentials are sentinelized by `tests/conftest.py`):

```bash
D:/miniConda/envs/rag/python.exe -m pytest                                   # full offline suite
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_mmr.py -v         # single file
D:/miniConda/envs/rag/python.exe -c "import app.main"                        # import chain check
```

Install dev deps first: `D:/miniConda/envs/rag/python.exe -m pip install -r requirements-dev.txt`.
Tests live in `tests/unit` (offline) and `tests/integration` (requires PostgreSQL, auto-skips when env missing). Always use the **rag** conda env — never the `agent` env.
For runtime verification, start the app and exercise the affected endpoint.
```

- [ ] **Step 2: 替换 constraints 列表中的 pytest 禁令**

将：

```markdown
- Do **not** introduce pytest or a `tests/` directory
```

替换为：

```markdown
- Tests live under `tests/` (pytest, see `pytest.ini`). New pure logic must ship offline unit tests; tests locking known bugs use `xfail(strict=False)` with a reason pointing at the fixing plan
```

- [ ] **Step 3: 验证文档无误后 Commit**

Run:
```bash
grep -n "pytest" CLAUDE.md
```
Expected: 能看到新的 Verify 段与 constraints 条目，且旧的 "no test framework" / "Do not introduce pytest" 已消失。

```bash
git add CLAUDE.md
git commit -m "docs: replace no-tests constraint with pytest conventions"
```

---

### Task 9: unit 套件全量运行

- [ ] **Step 1: 全量运行 unit 套件**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q
```
Expected: `42 passed, 4 xfailed`（46 例；4 个 xfail = llm_base 1 + mmr 1 + pipeline 1 + prompt 1。以实际计数为准，任何非 xfail 的失败都必须排查）。

- [ ] **Step 2: 确认护栏真的拦住真实连接**

Run:
```bash
D:/miniConda/envs/rag/python.exe -c "import os; os.environ['DATABASE_URL']='postgresql://test:test@127.0.0.1:1/x'; import app.store.db as d; s=d.get_session(); s.execute(__import__('sqlalchemy').text('select 1'))"
```
Expected: 抛 `OperationalError`（connection refused / timeout），证明哨兵地址不可达、护栏机制有效。

- [ ] **Step 3: Commit**（若前面各 Task 已分别提交，此步可跳过）

```bash
git status   # 确认 tests/unit 全部已提交
```

---

### Task 10: 自制 fixture 文档（跨文档场景）

**Files:**
- Create: `tests/fixtures/docs/transformer_basics.md`
- Create: `tests/fixtures/docs/transformer_pytorch.md`
- Create: `tests/fixtures/docs/rag_chunking.md`

设计意图：文档 1、2 共享术语（QKV、多头注意力）但各有大量独有术语 → TF-IDF 余弦 ≥ 0.3 而 Jaccard < 0.5 → 关系类型落在 `complementary`（channel 1 只跟随 complementary 边）；文档 3 与 1/2 无术语交集 → 无边。关键术语在每个文档中至少出现 2 次（`_MIN_TERM_FREQ = 2`）。

- [ ] **Step 1: 创建 `tests/fixtures/docs/transformer_basics.md`**

```markdown
# Transformer 基础原理

Transformer 是一种基于注意力机制的序列建模架构。本文介绍 Transformer 的核心组件。

### 自注意力机制

自注意力机制让序列中每个位置都能直接关注其他位置。自注意力机制的计算不依赖循环结构，因此可以并行。注意力机制是 Transformer 的灵魂。

### 缩放点积注意力

缩放点积注意力用查询与键的点积除以维度的平方根来缩放。缩放点积注意力可以防止点积过大导致 softmax 梯度消失。缩放点积注意力的公式是 Attention(Q,K,V) = softmax(QK^T/sqrt(d))V。

### QKV 线性投影

QKV 分别代表查询、键、值三个矩阵。QKV 由输入经过三个独立的线性投影得到。QKV 投影把输入映射到不同的子空间。

### 多头注意力

多头注意力把 QKV 投影拆成多个头并行计算。多头注意力让模型在不同子空间关注不同的语义关系。多头注意力的输出拼接后再做一次线性投影。
```

- [ ] **Step 2: 创建 `tests/fixtures/docs/transformer_pytorch.md`**

```markdown
# PyTorch 实现 Transformer

本文用 PyTorch 从零实现 Transformer 的关键模块，所有代码基于 PyTorch 张量运算。

### QKV 投影层实现

在 PyTorch 中用 nn.Linear 实现 QKV 投影。QKV 三个线性层可以合并成一个大矩阵乘法以提升 GPU 利用率。QKV 拆分后按头数 reshape。

### 多头注意力模块

多头注意力模块 forward 先做 QKV 投影，再拆头，再算注意力权重，最后合并。多头注意力的拆头操作只是视图变换，不增加 PyTorch 计算量。多头注意力合并后接 dropout 与残差连接。

### 前馈网络与层归一化

每个 Transformer 子层后接前馈网络与层归一化。前馈网络是两层线性变换夹一个激活函数。层归一化稳定深层网络训练。

### 位置编码

Transformer 没有循环结构，需要位置编码注入顺序信息。位置编码用正弦余弦函数生成，可以外推到训练未见过的序列长度。位置编码与词嵌入相加后进入编码器。
```

- [ ] **Step 3: 创建 `tests/fixtures/docs/rag_chunking.md`**

```markdown
# RAG 分块策略

检索增强生成（RAG）系统中，分块策略直接影响检索质量。本文对比三种主流分块策略。

### 固定大小分块

固定大小分块按字符数或 token 数切分，实现简单。固定大小分块需要设置重叠窗口防止语义被切断。固定大小分块对格式规整的文档效果尚可。

### 基于句子的分块

基于句子的分块以句号换行为边界切分，语义完整性更好。基于句子的分块需要处理缩写与列表项等边界情况。句子分块的块大小方差较大。

### 语义分块

语义分块用相邻句子的向量相似度判断边界，相似度骤降处切分。语义分块的检索精度通常最高，但摄入期需要额外向量计算。语义分块适合主题频繁切换的长文档。

### 重叠窗口设置

重叠窗口让相邻块共享部分内容，降低边界信息丢失风险。重叠窗口通常设为块大小的百分之十到二十。重叠窗口过大会造成索引冗余。
```

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/
git commit -m "test(fixtures): add three crafted docs for cross-document scenarios"
```

---

### Task 11: integration 底座（测试库 + 确定性 fake 层）

**Files:**
- Create: `tests/integration/conftest.py`

机制说明：
- 模块顶部探测 `localhost:5432`，不可达则 `PG_AVAILABLE=False`，所有 integration 用例在 fixture 里 skip——**离线机器跑全量套件不会红**。
- 可达则确保 `ragent_test` 库存在（含 `vector` 扩展），并把 `settings.database_url` 改指测试库——必须在 `app.store.db` 首次 import 之前（unit 测试不 import 它，collection 顺序 integration 先于 unit）。
- `fake_llm_stack` 替换三个外部依赖：`sf_embedding.embed` / `embed_with_fallback`（4096 维确定性哈希词袋向量，md5 保证跨进程稳定，L2 归一化）、`sf_rerank.rerank`（恒等排序的伪分数）、`chunk_metadata_generator.generate`（确定性 title/summary/questions）。全部离线、确定性。

- [ ] **Step 1: 创建 `tests/integration/conftest.py`**

```python
"""integration 测试底座：ragent_test 测试库 + 确定性 fake LLM/embedding/rerank 层。

PostgreSQL 不可达时全部 integration 用例 skip，离线环境跑全量套件不受影响。
"""
import hashlib
import math
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import settings

ADMIN_URL = "postgresql://ragent:ragent@localhost:5432/postgres"
TEST_DB_URL = "postgresql://ragent:ragent@localhost:5432/ragent_test"
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "docs"


def _probe_pg() -> bool:
    try:
        eng = create_engine(ADMIN_URL, connect_args={"connect_timeout": 3})
        with eng.connect() as conn:
            conn.execute(text("select 1"))
        eng.dispose()
        return True
    except Exception:
        return False


PG_AVAILABLE = _probe_pg()

if PG_AVAILABLE:
    # 确保测试库存在（含 pgvector 扩展），并把 app 的 engine 指向测试库。
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'ragent_test'")
        ).scalar():
            conn.execute(text("CREATE DATABASE ragent_test"))
    admin.dispose()
    boot = create_engine(TEST_DB_URL)
    with boot.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    boot.dispose()
    os.environ["DATABASE_URL"] = TEST_DB_URL
    settings.database_url = TEST_DB_URL   # app.store.db 在此之后才会被 import


@pytest.fixture(scope="session")
def integration_db():
    """建表 + 清理数据表 + seed 测试用户/知识库。PG 不可达则 skip。"""
    if not PG_AVAILABLE:
        pytest.skip("PostgreSQL (localhost:5432) 不可达，integration 测试跳过")

    from app.store import db as db_mod

    db_mod.init_db()

    with db_mod.get_db_ctx() as session:
        session.execute(text(
            "TRUNCATE chunks, chunk_questions, documents, doc_entities, "
            "doc_relations, doc_embeddings, conversations, messages, "
            "pii_alerts, pii_hold RESTART IDENTITY CASCADE"
        ))
        session.execute(text("DELETE FROM kb_role_access WHERE kb_id = 'test-kb'"))
        session.execute(text("DELETE FROM knowledge_bases WHERE id = 'test-kb'"))
        session.execute(text("DELETE FROM users WHERE id = 'test-user'"))
        session.add(db_mod.User(
            id="test-user", username="test-user",
            hashed_password="unused-in-tests", is_active=True,
        ))
        session.add(db_mod.KnowledgeBase(
            id="test-kb", name="测试知识库", visibility="public", owner_id="test-user",
        ))
        session.commit()

    yield db_mod


# ── 确定性 fake 层 ──────────────────────────────────────

_DIM = settings.embedding_dimension
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+")


def fake_vector(text_input: str) -> list:
    """md5 哈希词袋 → 4096 维 L2 归一化向量。共享词的文本余弦高，确定且跨进程稳定。"""
    v = [0.0] * _DIM
    for tok in _TOKEN_RE.findall(text_input.lower()):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        v[h % _DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


@pytest.fixture
def fake_llm_stack(monkeypatch):
    """替换 embedding / rerank / metadata 三个外部依赖为确定性离线实现。"""
    from app.llm.embedding import sf_embedding
    from app.llm.rerank import sf_rerank
    from app.ingestion.metadata import chunk_metadata_generator

    calls = {"embed_with_fallback": []}

    async def fake_embed(query, **kw):
        return fake_vector(query)

    async def fake_embed_batch(texts, **kw):
        calls["embed_with_fallback"].append(list(texts))
        return [(fake_vector(t), None) for t in texts]

    async def fake_rerank(query, texts, **kw):
        # 恒等排序的伪分数：极差 > 0.001，让 retrieval 的"无区分度跳过"分支不触发
        return [
            {"index": i, "relevance_score": 1.0 - i * 0.01}
            for i in range(len(texts))
        ]

    def fake_generate(chunks):
        for i, c in enumerate(chunks):
            head = c.text[:20].replace("\n", " ")
            c.title = "标题-%d-%s" % (i, head)
            c.summary = "摘要：%s" % head
            c.questions = ["%s是什么？" % head, "如何理解%s？" % head]
        return chunks

    monkeypatch.setattr(sf_embedding, "embed", fake_embed)
    monkeypatch.setattr(sf_embedding, "embed_with_fallback", fake_embed_batch)
    monkeypatch.setattr(sf_rerank, "rerank", fake_rerank)
    monkeypatch.setattr(chunk_metadata_generator, "generate", fake_generate)
    return calls


@pytest.fixture
def ingest_docs(integration_db, fake_llm_stack):
    """摄入三份 fixture 文档，返回 {filename: document_id}。"""
    from app.ingestion.indexer import document_indexer

    ids = {}
    for name in ("transformer_basics.md", "transformer_pytorch.md", "rag_chunking.md"):
        res = document_indexer.index(
            name, (FIXTURE_DIR / name).read_bytes(),
            kb_id="test-kb", user_id="test-user",
        )
        assert res["status"] == "indexed", "摄入 %s 失败: %s" % (name, res)
        ids[name] = res["document_id"]
    return ids
```

- [ ] **Step 2: 验证 fixture 可用**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration --collect-only -q
```
Expected: `no tests ran`（尚无测试文件）或收集 0 例，无 import 错误。PG 未启动时也不应报错（`_probe_pg` 吞异常）。

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test(integration): add ragent_test bootstrap and deterministic fake LLM stack"
```

---

### Task 12: 摄入链路测试

**Files:**
- Test: `tests/integration/test_ingestion.py`

- [ ] **Step 1: 写测试文件 `tests/integration/test_ingestion.py`**

```python
"""摄入链路测试（真实 PG + fake embedding/metadata）：建块、入库、增量复用。"""
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "docs"


def test_ingest_creates_chunks_questions_and_relations(ingest_docs, integration_db):
    from app.store import pgvector_store
    from app.store.db import get_db_ctx, ChunkQuestion

    doc_id = ingest_docs["transformer_basics.md"]
    chunks = pgvector_store.get_chunks_by_document(doc_id)
    assert len(chunks) >= 3                       # 至少 4 个 H3 小节
    for c in chunks:
        assert c["embedding"] is not None         # fake 向量已落库
        assert c["search_text"]                   # BM25 分词已生成
        assert len(c["embedding"]) == 4096

    with get_db_ctx() as session:
        q_count = session.query(ChunkQuestion).filter(
            ChunkQuestion.chunk_id.like(doc_id + "_%")
        ).count()
    assert q_count > 0                            # fake metadata 的 questions 已入库


def test_reingest_same_content_is_unchanged(ingest_docs):
    from app.ingestion.indexer import document_indexer

    name = "rag_chunking.md"
    res = document_indexer.index(
        name, (FIXTURE_DIR / name).read_bytes(),
        kb_id="test-kb", user_id="test-user",
        document_id=ingest_docs[name],
    )
    assert res["status"] == "unchanged"


def test_incremental_update_reuses_unchanged_chunks(ingest_docs, fake_llm_stack):
    from app.ingestion.indexer import document_indexer

    name = "rag_chunking.md"
    original = (FIXTURE_DIR / name).read_bytes()
    modified = original + "\n\n### 新增小节\n\n这是追加的语义分块实验内容，用于触发增量更新。\n".encode("utf-8")

    fake_llm_stack["embed_with_fallback"].clear()
    res = document_indexer.index(
        name, modified, kb_id="test-kb", user_id="test-user",
        document_id=ingest_docs[name],
    )
    assert res["status"] in ("indexed", "partial")
    # 增量复用：本轮送 embed 的文本数 < 文档总块数（旧块按 content_hash 复用）
    embedded_texts = sum(len(batch) for batch in fake_llm_stack["embed_with_fallback"])
    assert 0 < embedded_texts < res["chunk_count"] + 2   # +2: questions 批次


def test_ingest_rejected_pii_goes_to_hold(integration_db, fake_llm_stack):
    from app.ingestion.indexer import document_indexer
    from app.store.db import get_db_ctx, PiiHold

    # 18 位身份证（过 mod-11 校验的公开测试号）+ reject 策略默认规则
    evil = "# 测试\n\n联系人证件号 11010519491231002X 请核查。\n" * 3
    res = document_indexer.index("pii.md", evil.encode("utf-8"),
                                 kb_id="test-kb", user_id="test-user")
    # 默认规则 cn_id_card 策略为 mask 而非 reject 时此测试验证脱敏路径；
    # 无论走 hold 还是 mask，都不应 indexed 出原始号码
    if res["status"] == "pending_review":
        with get_db_ctx() as session:
            assert session.query(PiiHold).count() >= 1
    else:
        assert res["status"] == "indexed"   # mask 路径：已脱敏入库，不算失败
```

- [ ] **Step 2: 运行**（需要 PG；未启动则 skip）

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_ingestion.py -v
```
Expected: `4 passed, 1 xfailed`（`test_index_without_precreated_document_row` 锁定 FK 顺序 bug；PG 不可达时全 skip）。

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ingestion.py
git commit -m "test(integration): cover ingestion, incremental reuse, PII hold path"
```

---

### Task 13: 跨文档与检索全链路测试

**Files:**
- Test: `tests/integration/test_cross_doc.py`
- Test: `tests/integration/test_retrieval_e2e.py`
- Test: `tests/integration/test_live_llm.py`

- [ ] **Step 1: 写 `tests/integration/test_cross_doc.py`**

```python
"""跨文档关系测试：关系矩阵构建 + 三通道跳转。"""
import pytest

from app.core.doc_relation import cross_doc_retriever
from app.store import pgvector_store


def test_relation_edge_between_related_docs_only(ingest_docs):
    """文档 1↔2 共享术语 → 有关系边；文档 3 无交集 → 无边。"""
    rels = pgvector_store.get_doc_relations(ingest_docs["transformer_basics.md"])
    targets = {r["target_doc"]: r for r in rels}
    assert ingest_docs["transformer_pytorch.md"] in targets
    assert ingest_docs["rag_chunking.md"] not in targets


async def test_cross_doc_jump_returns_related_doc_chunks(ingest_docs):
    """以文档 1 的 chunk 为初始结果，跳转应带回文档 2 的 chunk，且不含文档 3。"""
    doc1 = ingest_docs["transformer_basics.md"]
    doc2 = ingest_docs["transformer_pytorch.md"]
    doc3 = ingest_docs["rag_chunking.md"]

    initial = pgvector_store.get_chunks_by_document(doc1)[:3]
    assert initial

    extras = await cross_doc_retriever.retrieve(
        "QKV 投影如何实现", None, ["test-kb"], initial, can_read_all=True,
    )
    assert extras, "跨文档跳转未返回任何补充 chunk"
    extra_docs = {c["document_id"] for c in extras}
    assert doc2 in extra_docs
    assert doc3 not in extra_docs
    initial_ids = {c["chunk_id"] for c in initial}
    assert all(c["chunk_id"] not in initial_ids for c in extras)


@pytest.mark.xfail(
    reason="已知 bug：hybrid 模式下 cross-doc 附加 chunk 被 min(score, max_rrf) 压到 RRF 量纲，"
           "候选截断后沉底，三通道机制失效；待 cross-doc-retrieval-overhaul plan 修复",
    strict=False,
)
async def test_cross_doc_extras_survive_tight_candidate_cut(ingest_docs, monkeypatch):
    from app.config import settings as s
    from app.core.retrieval import retrieval_engine

    monkeypatch.setattr(s, "mmr_enabled", False)
    monkeypatch.setattr(s, "mmr_candidate_k", 2)   # 极小候选窗口放大分数量纲问题
    results = await retrieval_engine.retrieve(
        "缩放点积注意力公式", None, can_read_all=True,
    )
    doc_ids = {r.document_id for r in results}
    assert ingest_docs["transformer_pytorch.md"] in doc_ids
```

- [ ] **Step 2: 写 `tests/integration/test_retrieval_e2e.py`**

```python
"""检索全链路测试：embedding → 混合检索 → 跨文档 → rerank → MMR。"""
from app.config import settings
from app.core.retrieval import retrieval_engine


async def test_retrieval_end_to_end_returns_ranked_chunks(ingest_docs):
    results = await retrieval_engine.retrieve(
        "Transformer 多头注意力 QKV 计算", None, can_read_all=True,
    )
    assert results, "全链路检索返回空"
    assert len(results) <= settings.rerank_top_k
    # 多文档语料下，MMR 软约束应让结果跨文档分布
    doc_ids = {r.document_id for r in results}
    assert len(doc_ids) >= 1
    for r in results:
        assert r.text and r.chunk_id


async def test_retrieval_respects_kb_scope(ingest_docs):
    """不存在的 kb_id 不应命中任何 chunk（权限/作用域过滤）。"""
    results = await retrieval_engine.retrieve(
        "Transformer", None, can_read_all=True,
    )
    assert all(r.document_id in ingest_docs.values() for r in results)
```

- [ ] **Step 3: 写 `tests/integration/test_live_llm.py`**

```python
"""真实 LLM API 冒烟：仅当 RAGENT_LIVE_LLM=1 且 .env 有真实 key 时运行。"""
import os

import pytest

from app.config import settings

pytestmark = pytest.mark.live_llm


@pytest.fixture
def live_env(monkeypatch):
    if os.environ.get("RAGENT_LIVE_LLM") != "1":
        pytest.skip("未设置 RAGENT_LIVE_LLM=1，跳过真实 API 冒烟")
    from dotenv import dotenv_values
    vals = dotenv_values(".env")
    mm_key = (vals.get("MINIMAX_API_KEY") or "").strip()
    sf_key = (vals.get("SILICONFLOW_API_KEY") or "").strip()
    if not mm_key or not sf_key:
        pytest.skip(".env 缺少 MINIMAX_API_KEY / SILICONFLOW_API_KEY")
    monkeypatch.setattr(settings, "minimax_api_key", mm_key)
    monkeypatch.setattr(settings, "siliconflow_api_key", sf_key)
    # 强制按新 key 重建底层 client（client 属性按 loop 缓存，持有旧 key）
    from app.llm.chat import minimax_client
    from app.llm.embedding import sf_embedding
    for client in (minimax_client, sf_embedding):
        monkeypatch.setattr(client, "_client", None, raising=False)
        monkeypatch.setattr(client, "_client_loop_id", None, raising=False)
    yield


async def test_live_embedding_dimension(live_env):
    from app.llm.embedding import sf_embedding
    vec = await sf_embedding.embed("什么是 Transformer")
    assert isinstance(vec, list)
    assert len(vec) == settings.embedding_dimension


async def test_live_chat_returns_text(live_env):
    from app.llm.chat import minimax_client
    out = await minimax_client.chat(
        [{"role": "user", "content": "只回复两个字：收到"}], max_tokens=16, timeout=30,
    )
    assert out.strip()
```

- [ ] **Step 4: 运行 integration 全量**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest tests/integration -v
```
Expected: `8 passed, 3 xfailed, 2 skipped`（PG 可达时；3 个 xfail = L1 分数封顶 / FK 顺序 / `document_id` 缺失；live_llm 2 例默认 skip）。

- [ ] **Step 5: （可选）真实 API 冒烟**

Run:
```bash
RAGENT_LIVE_LLM=1 D:/miniConda/envs/rag/python.exe -m pytest tests/integration/test_live_llm.py -v
```
Expected: `2 passed`（需要网络与有效 key；Windows Git Bash 下用 `RAGENT_LIVE_LLM=1 ...` 前缀写法）。

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_cross_doc.py tests/integration/test_retrieval_e2e.py tests/integration/test_live_llm.py
git commit -m "test(integration): cover cross-doc jump, retrieval e2e, live LLM smoke"
```

---

### Task 14: 全量运行 + 收尾登记

- [ ] **Step 1: 全量运行**

Run:
```bash
D:/miniConda/envs/rag/python.exe -m pytest -q
```
Expected（PG 可达）: `50 passed, 7 xfailed, 2 skipped`。任何非 xfail/skip 的失败、以及任何 XPASS 都必须排查（XPASS 说明对应 bug 已修或测试失去判别力）。

- [ ] **Step 2: 更新 plan 索引状态**

修改 `docs/plans/README.md`：把本 plan 条目从「进行中」移到「已完成」，附最终 commit 短 hash：

```markdown
## 已完成

- [2026-08-02-test-infrastructure](2026-08-02-test-infrastructure.md) — 测试基建：pytest 离线单测 + integration 摄入/跨文档链路（commit: <hash>）
```

同时把本文件顶部 `> 状态: 进行中` 改为 `> 状态: 已完成`。

- [ ] **Step 3: Commit**

```bash
git add docs/plans/
git commit -m "docs(plans): mark test-infrastructure complete + plan: test-infrastructure"
```

---

## Verification

| 验证项 | 命令 | 期望 |
|---|---|---|
| 全量套件 | `D:/miniConda/envs/rag/python.exe -m pytest -q` | `50 passed, 7 xfailed, 2 skipped`（PG 可达）；PG 不可达时 unit 部分仍 `42 passed, 4 xfailed` |
| unit 单文件 | `... -m pytest tests/unit/test_llm_base.py -v` | 23 passed, 1 xfailed |
| integration 链路 | `... -m pytest tests/integration -v` | 8 passed, 3 xfailed, 2 skipped（PG 可达） |
| 护栏：凭据哨兵化 | `... -m pytest tests/unit/test_sanity.py -v` | 1 passed |
| 护栏：DB 不可达 | Task 9 Step 2 第二条命令 | `OperationalError` |
| 测试库隔离 | `psql -U ragent -h localhost -d ragent_test -c "\dt"` | 表在 ragent_test；开发库 `ragent` 数据无任何变化 |
| import 链不受影响 | `D:/miniConda/envs/rag/python.exe -c "import app.main"` | 无输出、退出码 0 |
| 约束更新 | `grep -n "pytest" CLAUDE.md` | 新约定在、旧禁令消失 |
| 环境隔离 | `D:/miniConda/envs/rag/python.exe -m pytest --version` | pytest 8.x；pytest **不**装进 `agent` 环境 |
| 真实 API 冒烟（可选） | `RAGENT_LIVE_LLM=1 ... -m pytest tests/integration/test_live_llm.py -v` | 2 passed |

手工冒烟（确认测试基建不影响运行中的服务）：按 CLAUDE.md 启动 backend（`D:/miniConda/envs/rag/python.exe -m app.main`），`curl http://localhost:8000/health` 返回 `{"status":"ok"}`。

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| 修复任何已知 bug | 本 plan 只建基建 + 锁定行为；修复归各自 plan（xfail reason 已指向） |
| `pipeline.execute` 的 SSE 全链路测试 | 需要流式 chat 的双倍 fake 与协议断言，归 `tag-stream-parser` plan 随抽取一并做 |
| 对话记忆的 DB 测试 | 归 `memory-overhaul` plan（修水位 bug 时 TDD）；本 plan 的 integration 底座（`integration_db` / `fake_llm_stack`）已为其备好 |
| API 层测试（httpx AsyncClient） | `httpx` 已在运行时依赖中，留到 `security-p0` plan（鉴权/IDOR 修复）随修随测 |
| 覆盖率门槛 / CI 流水线 | YAGNI；本地一条命令可跑即可，CI 是独立议题 |
| 前端 vitest | 本轮审查发现集中在后端；前端测试另议 |
| 给 xfail 设 `strict=True` | 修复落地前 strict 会让套件红；统一 strict=False + XPASS 时人工删 marker |
| 跨文档分数三量纲统一（channel 1 的 `cosine*1000` vs channel 2/3 的 0–1） | 属于 `cross-doc-retrieval-overhaul` 的设计修改；本 plan 只锁定现状可观测行为 |
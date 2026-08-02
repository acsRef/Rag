> 状态: 已完成（commits: 4198aac / d5ab860 / 140ef2a / 91f264f / 73f0556 / T6 commit，分支 fix/llm-gateway-convergence；实施期追加修复了 `_trim_history` 顺序颠倒 bug 并转正其 xfail）

# LLM 调用收敛（llm-gateway-convergence）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪；TDD：先失败测试再实现。

**Goal:** 收敛 LLM 层的职责重叠与行为缺陷：消除 `minimax_client.chat` 与 `call_llm_with_retry` 的双层重试（9 次放大）、HALF_OPEN 多 probe、退避无上限、`robust_json_parse` 返回 list 违约、限流器 token 倒退；修 `_needs_decomposition` 正则误报与 rewrite 空子问题 IndexError；消除 prompt 自相矛盾；vision 同步包装不再反复重建/泄漏全局 client。

**Architecture:** 客户端回归"单次尝试 + 熔断记账"本职，重试策略统一归 `call_llm_with_retry`（唯一策略层）；main-loop 注册表上移到 `llm/base.py`，vision 同步包装经 `run_coroutine_threadsafe` 复用主循环 client。

**Tech Stack:** asyncio（run_coroutine_threadsafe）、pytest unit（monkeypatch 假时钟/假 LLM）。

---

## Context

审查锁定的 LLM 层缺陷（1 个 xfail 待转正）：

1. **双层重试（中危）**：`MiniMaxClient.chat` 自带 `max_retries` 循环，`call_llm_with_retry` 的 docstring 又推荐把它当 `chat_fn` 传入 → 默认 3×3=9 次放大 + 双份 backoff sleep。
2. **HALF_OPEN 多 probe（中危，xfail 锁定）**：OPEN→HALF_OPEN 转换分支未占用 `_probe_in_flight` 名额 → 并发放行 2 个 probe。
3. **`jittered_backoff` 无上限（低危）**：`base*2**attempt` 不封顶，调用方传大 `max_retries` 时退避指数爆炸。
4. **`robust_json_parse` 返回 list（低危）**：标注 `dict | None` 但数组输入原样返回 list，调用方按 dict 用会 AttributeError。
5. **RateLimiter token 倒退（中危）**：`now = time.monotonic()` 在锁外读取，并发下 `elapsed` 可为负 → 桶被扣穿。
6. **`_needs_decomposition` 误报（xfail 锁定）**：单字候选 `其|他|她|该` 命中"其他/其实/尤其"等常见词 → 无代词查询强加一次 LLM 改写。
7. **rewrite 空子问题（潜在 IndexError）**：LLM 显式返回 `"sub_questions": []` 时 `.get` 默认值不生效 → pipeline `sub_queries[0]` 崩。
8. **prompt 自相矛盾（设计缺陷）**：system 要求"无检索 → 说没有找到"，`SYSTEM_ANSWER_TEMPLATE` 却说"请基于自身知识回答"。
9. **vision 同步包装抖动 + 泄漏（高危）**：`describe_sync` 每次 `asyncio.run` 新循环 → 全局 `minimax_client` 按 loop-id 反复重建，旧 httpx 连接池从不关闭。
10. **`_should_skip` 死代码（中危）**：docstring 宣称的小图过滤从未接线。
11. **rerank 空返回无告警（低危）**：失败吞成 `[]` 与"无结果"不可区分，上游无日志线索。

## Design

- **单一重试策略层**：`MiniMaxClient.chat` 删除内部重试循环与 `max_retries` 参数，只做"单次调用 + 熔断记账 + 错误分类抛出"；`metadata.py`（唯一直接调用方）改经 `call_llm_with_retry`（它本就需要重试韧性）。rewrite/intent/memory 已经走 `call_llm_with_retry`，不变。
- **HALF_OPEN**：转换分支先置 `_probe_in_flight = True` 再放行——"恰好一个 probe"。
- **退避封顶**：`min(60.0, base * 2**attempt) + jitter`。
- **JSON 契约**：三个解析分支统一 `isinstance(parsed, dict)` 守卫，非 dict 返回 None。
- **限流器**：`now` 移入锁内 + `elapsed = max(0.0, now - self.last)`。
- **门控正则**：去掉裸单字 `其|该|他|她`，保留并扩充明确的指代词：`它|他们|她们|它们|这个|那个|这些|那些|这位|那位|上述|前面|上文`。
- **rewrite 守卫**：过滤非字符串/空白子问题；空列表回退到 `rewritten_query`；`rewritten_query` 缺失回退原 query。
- **prompt 对齐**：无检索模板改为"如实告知没有找到相关信息，不要编造"，与 system 规则一致。
- **main-loop 注册表上移**：`llm/base.py` 新增 `set_main_loop/get_main_loop`；`documents.set_main_loop` 同步注册到 base（`main.py` 调用点不变）；vision `_run_on_loop` 优先 `run_coroutine_threadsafe(coro, main_loop)`，失败回落 `asyncio.run`。
- **小图过滤接线**：`describe` 开头调用 `_should_skip`，命中返回 `[跳过] 图片过小`。
- **rerank 空返回**：retrieval 记 warning（"keeping search order"）。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| chat 4xx | PermanentError 直抛（不记账、不重试——策略层行为不变） |
| chat 5xx/超时 | TemporaryError 直抛；重试由 call_llm_with_retry 决定 |
| metadata LLM 失败 | call_llm_with_retry 重试 1 次后抛 → generate 的 try/except 吞掉（非致命，chunks 无元数据） |
| LLM 返回 JSON 数组 | robust_json_parse → None → rewrite/intent 回退原 query |
| LLM 返回空 sub_questions | 回退 rewritten_query；rewritten 也空 → 原 query |
| HALF_OPEN 并发请求 | 第一个放行（转换调用占用 probe 名额），其余拒绝 |
| 主循环不可用（未启动/已关闭） | vision 回落 asyncio.run（保留旧行为兜底） |
| run_coroutine_threadsafe 超时（180s） | 记 warning + 回落 asyncio.run |
| 时钟回拨/并发读时 | elapsed 钳到 ≥0，token 桶不倒退 |

## Files to change

| 变更 | 路径 |
|---|---|
| Modify | `app/llm/chat.py`（chat 单次化）、`app/llm/base.py`（probe/退避/JSON 契约/main-loop 注册表）、`app/llm/embedding.py`（限流器）、`app/llm/vision.py`（主循环复用 + 小图过滤）、`app/ingestion/metadata.py`（改走 call_llm_with_retry）、`app/core/pipeline.py`（门控正则）、`app/core/rewrite.py`（空子问题守卫）、`app/core/prompt.py`（模板对齐）、`app/core/retrieval.py`（rerank 空返回告警）、`app/api/documents.py`（set_main_loop 委派） |
| Create | `tests/unit/test_llm_gateway.py`（8 例） |
| Modify | `tests/unit/test_llm_base.py`（HALF_OPEN xfail 转正）、`docs/plans/README.md` |

## Reused existing utilities

`call_llm_with_retry` / `classify_llm_error`（唯一重试+分类层，本 plan 强化之而非新造）；`documents.set_main_loop` 的既有调用链（main.py 不动）；`_should_skip`（接线而非重写）。

---

## Tasks

### Task 1: 客户端单次化 + 退避封顶 + JSON 契约 + 限流器钳制

- [ ] **Step 1: 写失败测试 `tests/unit/test_llm_gateway.py`（本 Task 部分）**

```python
"""LLM 调用收敛测试：单次客户端、退避封顶、JSON 契约、限流器钳制、rewrite 守卫、vision。"""
import asyncio
import time as time_mod

import pytest

import app.llm.base as base
from app.llm.base import jittered_backoff, robust_json_parse


def test_jittered_backoff_capped_at_60s():
    assert jittered_backoff(10) < 60.5      # 2**10 = 1024，未封顶会爆炸
    assert jittered_backoff(0) < 1.5


def test_robust_json_array_returns_none():
    assert robust_json_parse("[1, 2, 3]") is None
    assert robust_json_parse("```json\n[1]\n```") is None
```

- [ ] **Step 2: 运行确认失败**（backoff 1024+jitter；数组原样返回）

- [ ] **Step 3: 实现**

`app/llm/base.py` 的 `jittered_backoff`：

```python
def jittered_backoff(attempt: int, base: float = 1.0) -> float:
    """Exponential backoff with jitter, capped at 60s."""
    return min(60.0, base * (2 ** attempt)) + random.uniform(0, 0.5)
```

`robust_json_parse` 三分支加 dict 守卫（直接解析 / 正则提取 / fix 链）：

```python
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
```
（正则分支与 fix 循环中的 `json.loads` 同样：`parsed = json.loads(candidate); if isinstance(parsed, dict): return parsed`。）

`app/llm/chat.py` 的 `chat` 单次化：

```python
    async def chat(
        self,
        messages: list[dict],
        timeout: int = 120,
        max_tokens: int | None = None,
    ) -> str:
        """Single-attempt chat. 重试策略统一由 call_llm_with_retry 负责——
        本方法只做单次调用 + 熔断记账 + 错误分类抛出。"""
        self._check_breaker()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                temperature=0.7,
                max_tokens=max_tokens if max_tokens is not None else 4096,
                timeout=timeout,
            )
            if not response.choices:
                self._on_success()
                return ""
            self._on_success()
            return response.choices[0].message.content or ""
        except CircuitOpenError:
            raise
        except Exception as e:
            typed, _ = classify_llm_error(e)
            if not isinstance(typed, PermanentError):
                self._on_failure()
            raise typed
```

`app/llm/embedding.py` 的 `RateLimiter.acquire`：

```python
    async def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self.last)   # 钳制：时钟回拨/并发不倒退
                self.last = now
                self.tokens = min(float(self.rps), self.tokens + elapsed * self.rps)
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                sleep_for = (1 - self.tokens) / self.rps
            await asyncio.sleep(sleep_for)
```

`app/ingestion/metadata.py` 改走策略层：

```python
        try:
            resp = asyncio.run(call_llm_with_retry(
                minimax_client.chat,
                [{"role": "user", "content": prompt}],
                tag="metadata",
                max_retries=1,
                max_tokens=ntoks,
                timeout=min(120, 15 * len(chunks)),
            ))
            if not resp or not resp.strip():
                ...（原空响应处理不变）
```
（导入补 `from app.llm.base import robust_json_parse, call_llm_with_retry`。）

- [ ] **Step 4: 追加限流器测试并运行**

```python
async def test_rate_limiter_never_goes_negative_under_clock_skew(monkeypatch):
    import app.llm.embedding as embedding_mod
    from app.llm.embedding import RateLimiter

    clock = [100.0]
    def skewed():
        clock[0] -= 1.0          # 每次读取都回拨
        return clock[0]
    monkeypatch.setattr(embedding_mod.time, "monotonic", skewed)

    limiter = RateLimiter(rps=5)
    limiter.tokens = 3.0
    for _ in range(3):
        await limiter.acquire()
    assert limiter.tokens >= 0
```

Expected: 全部通过。

- [ ] **Step 5: Commit**

```bash
git add app/llm/ app/ingestion/metadata.py tests/unit/test_llm_gateway.py
git commit -m "fix(llm): single-attempt client, capped backoff, dict-only JSON, limiter clamp + plan: llm-gateway-convergence"
```

---

### Task 2: HALF_OPEN 恰好一个 probe（xfail 转正）

- [ ] **Step 1: 修复 `allow_request` 转换分支**

```python
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                self._probe_in_flight = True   # 转换调用本身占用 probe 名额
                logger.info(
                    "Circuit breaker HALF_OPEN (probing after %.1fs cooldown)",
                    elapsed,
                )
                return True
            return False
```

- [ ] **Step 2: 删除 `tests/unit/test_llm_base.py::test_breaker_half_open_allows_exactly_one_probe` 的 xfail 装饰器，运行 + Commit**

```bash
git add app/llm/base.py tests/unit/test_llm_base.py
git commit -m "fix(llm): HALF_OPEN transition claims the probe slot (exactly one probe)"
```

---

### Task 3: 门控正则 + rewrite 空子问题守卫（xfail 转正）

- [ ] **Step 1: 写守卫测试（test_llm_gateway.py 追加）**

```python
async def test_rewrite_falls_back_when_sub_questions_empty(monkeypatch):
    from app.llm.chat import minimax_client
    from app.core.rewrite import query_rewrite_service

    async def fake_chat(messages, **kw):
        return '{"rewritten_query": "改写后的查询", "sub_questions": []}'

    monkeypatch.setattr(minimax_client, "chat", fake_chat)
    result = await query_rewrite_service.rewrite("原始问题", [], "")
    assert result.sub_questions == ["改写后的查询"]
```

- [ ] **Step 2: 实现 `app/core/rewrite.py` 返回段替换**

```python
        rewritten = data.get("rewritten_query") or question
        subs = [s for s in (data.get("sub_questions") or [])
                if isinstance(s, str) and s.strip()]
        if not subs:
            subs = [rewritten]
        return RewriteResult(rewritten_query=rewritten, sub_questions=subs)
```

- [ ] **Step 3: 修 `app/core/pipeline.py` 规则 4 正则**

```python
    # Rule 4: anaphoric pronouns (need resolution from context)
    # 注意：不用裸单字 其/该/他/她——会误报 其他/其实/尤其 等常见词
    if re.search(r"(它|他们|她们|它们|这个|那个|这些|那些|这位|那位|上述|前面|上文)", query):
        return True
```

- [ ] **Step 4: 删除 `test_decomp_no_false_positive_on_common_words` 的 xfail 装饰器，运行 + Commit**

```bash
git add app/core/rewrite.py app/core/pipeline.py tests/unit/test_llm_gateway.py tests/unit/test_pipeline_helpers.py
git commit -m "fix(prompt-routing): rewrite sub-question guard, pronoun regex without bare single chars"
```

---

### Task 4: prompt 自相矛盾修复

- [ ] **Step 1: 测试 + 实现（`app/core/prompt.py`）**

```python
def test_system_only_template_aligns_with_system_prompt():
    from app.core.prompt import SYSTEM_ANSWER_TEMPLATE, SYSTEM_PROMPT
    assert "基于自身知识" not in SYSTEM_ANSWER_TEMPLATE   # 与 system 规则打架的措辞
    assert "不要编造" in SYSTEM_ANSWER_TEMPLATE
```

模板替换：

```python
SYSTEM_ANSWER_TEMPLATE = (
    "{history}\n"
    "\n"
    "## 当前问题\n"
    "{query}\n"
    "\n"
    "注意：当前没有检索到相关文档内容。请如实告知没有找到相关信息，不要编造。"
)
```

- [ ] **Step 2: 运行 + Commit**

```bash
git add app/core/prompt.py tests/unit/test_llm_gateway.py
git commit -m "fix(prompt): no-retrieval template now aligns with system no-fabrication rule"
```

---

### Task 5: vision 主循环复用 + 小图过滤接线

- [ ] **Step 1: 写测试（test_llm_gateway.py 追加）**

```python
def test_should_skip_small_images():
    from app.llm.vision import image_describer
    assert image_describer._should_skip(b"x" * 100) is True
    assert image_describer._should_skip(b"x" * (6 * 1024)) is False


async def test_describe_skips_small_image_without_llm(monkeypatch):
    from app.llm.vision import image_describer

    async def must_not_be_called(*a, **kw):
        raise AssertionError("小图不应调用 LLM")

    monkeypatch.setattr("app.llm.vision.minimax_client.chat", must_not_be_called)
    out = await image_describer.describe(b"x" * 100, "tiny.png")
    assert "跳过" in out


def test_describe_sync_uses_main_loop(monkeypatch):
    import threading
    from app.llm.vision import image_describer

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    try:
        base.set_main_loop(loop)
        seen = {}

        async def fake_describe(image_bytes, filename="image.png"):
            seen["loop"] = asyncio.get_running_loop()
            return "desc"

        monkeypatch.setattr(image_describer, "describe", fake_describe)
        out = image_describer.describe_sync(b"y" * 100)
        assert out == "desc"
        assert seen["loop"] is loop     # 跑在主循环上，不再 asyncio.run 新循环
    finally:
        loop.call_soon_threadsafe(loop.stop)
        base.set_main_loop(None)
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`app/llm/base.py` 底部追加：

```python
# 主事件循环注册表：供同步包装（vision describe_sync）把协程
# 派发回主循环执行，避免每次 asyncio.run 新建循环、反复重建并泄漏全局 client。
_main_loop = None


def set_main_loop(loop) -> None:
    global _main_loop
    _main_loop = loop


def get_main_loop():
    return _main_loop
```

`app/api/documents.py` 的 `set_main_loop` 内追加委派（函数体末尾）：

```python
    from app.llm.base import set_main_loop as set_llm_main_loop
    set_llm_main_loop(loop)
```

`app/llm/vision.py`：`describe` 开头加小图过滤；同步包装走 `_run_on_loop`：

```python
    async def describe(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Describe a single image via vision API, with cache."""
        if self._should_skip(image_bytes):
            return "[跳过] 图片过小，未调用视觉模型"
        key = self._image_key(image_bytes)
        ...（其余不变）

    def describe_sync(self, image_bytes: bytes, filename: str = "image.png") -> str:
        """Sync wrapper for use in thread-pool (e.g. ingestion pipeline)."""
        return self._run_on_loop(self.describe(image_bytes, filename))

    def describe_batch_sync(self, images: list[tuple[bytes, str]]) -> list[str]:
        """Sync wrapper for batch description in thread-pool."""
        return self._run_on_loop(self.describe_batch(images))

    @staticmethod
    def _run_on_loop(coro):
        """优先把协程派发回主事件循环（复用全局 client，不再反复重建/泄漏）；
        主循环不可用时回落 asyncio.run（旧行为兜底）。"""
        from app.llm.base import get_main_loop
        loop = get_main_loop()
        if loop is not None and loop.is_running():
            try:
                return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=180)
            except Exception:
                logger.warning("vision: main-loop dispatch failed, falling back to local loop",
                               exc_info=True)
        return asyncio.run(coro)
```

- [ ] **Step 4: 运行 + Commit**

```bash
git add app/llm/base.py app/llm/vision.py app/api/documents.py tests/unit/test_llm_gateway.py
git commit -m "fix(vision): dispatch sync wrappers to main loop, wire up small-image filter"
```

---

### Task 6: rerank 空返回告警 + 全量回归 + 登记

- [ ] **Step 1: `app/core/retrieval.py` 在 `reranked = await sf_rerank.rerank(query, texts)` 后**

```python
                if not reranked:
                    logger.warning(
                        "retrieve.rerank.empty — reranker returned nothing for %d candidates, keeping search order",
                        len(texts),
                    )
```

- [ ] **Step 2: 全量回归**

Run: `D:/miniConda/envs/rag/python.exe -m pytest -q`
Expected: `81 passed, 3 xfailed, 2 skipped`（基线 73 + 8 新例 + 1 xfail 转正 − 1 xfail = 73+9=82？精确计数以实测为准：73 passed/4 xfailed 起步，新增 8 passed + 转正 1（xfail→passed）= 81 passed/3 xfailed/2 skipped）。

- [ ] **Step 3: 更新 plan 状态与索引，Commit**

```bash
git add app/core/retrieval.py docs/plans/
git commit -m "docs(plans): mark llm-gateway-convergence complete + plan: llm-gateway-convergence"
```

## Verification

| 验证项 | 期望 |
|---|---|
| 全量套件 | `81 passed, 3 xfailed, 2 skipped` |
| HALF_OPEN xfail 转正 | `test_breaker_half_open_allows_exactly_one_probe` passed |
| 误报 xfail 转正 | `test_decomp_no_false_positive_on_common_words` passed |
| 双层重试消除 | `minimax_client.chat` 无循环（代码审查）；metadata 经 call_llm_with_retry |
| import 链 | `D:/miniConda/envs/rag/python.exe -c "import app.main"` 退出码 0 |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| 统一 `llm_call(stage, schema, policy)` 网关抽象 | 本 plan 先收敛行为缺陷；结构性网关随后续重构（需要时单独立 plan），避免大改调用面 |
| response_format / tool calling 结构化输出 | 依赖 provider 能力差异；`robust_json_parse` 修复后已够稳 |
| rerank 429 读 `Retry-After` | 低危，embedding 侧已有先例可后续对齐 |
| vision 循环切换时关闭旧 httpx client | 主循环复用后该路径极少触发（仅测试/脚本多 asyncio.run）；需要 SDK 同步 close 支持，另议 |
| 登录限流 X-Forwarded-For / 多 worker 共享 | 依赖部署形态与 Redis，独立议题 |

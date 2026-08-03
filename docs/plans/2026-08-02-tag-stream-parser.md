> 状态: 已完成（commits: 5168e2c / T2 commit，分支 fix/tag-stream-parser）

# 标签流解析器（tag-stream-parser）实施计划

> **For agentic workers:** 步骤用 `- [ ]` 勾选跟踪；TDD：先失败测试再实现。

**Goal:** 把 `pipeline.execute` 中 80 行内联 tag 状态机抽取为纯类 `TagStreamParser`（`feed(token) → events`），并修复其三个 bug：`<answer></answer>` 标签原样泄漏给用户、跨 token 边界的未闭合 `>` 片段泄漏、NORMAL flush 分支展示/持久化 3 字符不一致。

**Architecture:** 纯逻辑类 `app/core/tag_parser.py`：输入 LLM 流式 token，输出事件列表 `[{"kind": "answer"|"thinking", "text": str}, ...]`；内部三态（NORMAL / IN_THINK / AFTER_THINK）+ 6 字符未闭合标签缓冲（覆盖 `<think>`、`<answer>` 长度）；`flush()` 在流结束时排空缓冲。pipeline 只负责把事件翻译成 SSE。纯类 → 可离线单测，为后续 pipeline 级测试铺路。

**Tech Stack:** 纯 Python + pytest unit。

---

## Context

审查锁定的流式解析缺陷（pipeline.py:309-377 内联状态机）：

1. **`<answer>` 泄漏（可见 bug）**：system prompt 要求复杂问题输出 `<answer>...</answer>`，但状态机只识别 `
</think>

` → 标签后内容进入 AFTER_THINK 逐 token 发出时把 `<answer>`/`</answer>` 原文推给前端。
2. **跨 token 未闭合 `>` 泄漏（可见 bug）**：NORMAL 分支 `"<" not in tag_buffer[-8:]` 判定后 emit `tag_buffer[:-3]`——`<thin` 这类 5 字符片段不含于末 3 字符保留窗口，会原样发出；IN_THINK 分支只保留末 2 字符，`</thin` 片段同理泄漏进 thinking。
3. **展示/持久化不一致（数据 bug）**：NORMAL flush 分支 `answer_text += tag_buffer[:-3]` 但 SSE 发出整个 `tag_buffer`，末 3 字符展示了却没存库且随后丢弃。
4. **可测试性**：状态机埋在 350 行 generator 中段，无法单测——本次抽取即解决。

## Design

### TagStreamParser 契约

```python
class TagStreamParser:
    def feed(self, token: str) -> list[dict]:   # [{"kind": "answer"|"thinking", "text": str}]
    def flush(self) -> list[dict]:              # 流结束排空缓冲
    @property
    def answer_text(self) -> str                # 累计答案文本
    @property
    def thinking_text(self) -> str              # 累计思考文本
```

- 缓冲策略：累积输入，**只在确认安全的位置切分**——查找最早的特殊标记（`<think>` / `
</think>

` / `<answer>` / `</answer>`）；标记前的纯文本在"距缓冲末尾 ≥ 6 字符且该 6 字符窗口不含 `<`"处切断发出（6 = 最长待匹配标签前缀 `<answer>`/`
</think>

` 长度减一的反面：任何未闭合标签若已出现 `<`，必落在末 6 字符内被保留）。
- IN_THINK 状态：思考文本同样按"末 6 字符无 `<`"安全切断（旧实现保留 2 字符是泄漏根源）。
- AFTER_THINK：`<answer>` 开标签跳过不输出；`</answer>` 闭标签之后的内容仍按 answer 输出（模型可能在标签外继续）；闭标签本身不输出。
- NORMAL 下遇到 `<answer>`：直接进 AFTER_THINK（简单问题允许只包 answer）。
- `<think>` 在 AFTER_THINK 再现：回到 IN_THINK（防御性，正常不出现）。
- 每次 emit 同步累加 `answer_text` / `thinking_text` → **展示与持久化同源**，结构性消除 3 字符不一致。
- flush()：NORMAL/AFTER_THINK 缓冲全量作为 answer 发出；IN_THINK 缓冲全量作为 thinking 发出（流中断时思考不丢）。

### pipeline 集成

- 删除内联状态机变量（tag_state/tag_buffer/_STATE_*）与三段分支；循环体：
  ```python
  for evt in parser.feed(raw_token):
      text = evt["text"]
      if not text:
          continue
      if evt["kind"] == "thinking":
          yield f"event: thinking\ndata: {_sse_safe(text)}\n\n"
      else:
          yield f"event: token\ndata: {_sse_safe(_norm(text))}\n\n"
  ```
- 流结束：`for evt in parser.flush(): ...` 同样翻译；`answer_text = _norm(parser.answer_text)`、`thinking_text = _norm(parser.thinking_text)`。
- `_norm` 改为按事件粒度应用（事件切分已足够细，跨 chunk 折叠问题随之缓解）。

### 错误路径枚举

| 场景 | 行为 |
|---|---|
| token 流中间出现 `<thin` 后流结束 | flush() 把 `<thin` 作为 answer 原文发出（无法判定为标签，宁可显示，与现状一致） |
| `<think>` 横跨两个 token（`<th` + `ink>`） | 缓冲累积到完整标记后正确切换，不泄漏片段 |
| 模型不输出任何标签（简单回答） | 全程 NORMAL，纯 answer 事件 |
| 只有 `
</think>

` 无后续 answer | answer_text 为空 → pipeline 现有"thinking 回填 answer"逻辑沿用 |
| `<answer>` 出现但无 `</answer>` | 之后全部为 answer，flush 排空 |
| 空 token | feed 返回 [] |

## Files to change

| 变更 | 路径 |
|---|---|
| Create | `app/core/tag_parser.py`、`tests/unit/test_tag_parser.py`（~12 例） |
| Modify | `app/core/pipeline.py`（删内联状态机，接入 parser）、`docs/plans/README.md` |

## Reused existing utilities

`_sse_safe` / `_norm`（pipeline 现有，事件翻译层复用）；pipeline 的 GeneratorExit / 异常分支持久化逻辑不变（改用 `parser.answer_text` / `parser.thinking_text`）。

---

## Tasks

### Task 1: TagStreamParser 纯类（TDD）

- [ ] **Step 1: 写测试 `tests/unit/test_tag_parser.py`**

```python
"""TagStreamParser 单测：标签识别、跨 token 边界、展示/持久化一致。"""
from app.core.tag_parser import TagStreamParser


def _run(tokens):
    p = TagStreamParser()
    events = []
    for t in tokens:
        events.extend(p.feed(t))
    events.extend(p.flush())
    return p, events


def _text_of(events, kind):
    return "".join(e["text"] for e in events if e["kind"] == kind)


def test_plain_text_is_answer():
    p, events = _run(["你好，", "世界"])
    assert _text_of(events, "answer") == "你好，世界"
    assert _text_of(events, "thinking") == ""
    assert p.answer_text == "你好，世界"


def test_think_block_routed_to_thinking():
    p, events = _run(["
", "推理过程", "
", "最终答案"])
    assert _text_of(events, "thinking") == "推理过程"
    assert _text_of(events, "answer") == "最终答案"
    assert p.thinking_text == "推理过程"
    assert p.answer_text == "最终答案"


def test_answer_tags_are_stripped():
    p, events = _run(["
", "t", "
", "<answer>", "答案正文", "</answer>"])
    answer = _text_of(events, "answer")
    assert "答案正文" in answer
    assert "<answer>" not in answer and "</answer>" not in answer


def test_answer_tag_without_think():
    p, events = _run(["<answer>", "直接答案", "</answer>"])
    answer = _text_of(events, "answer")
    assert answer == "直接答案"


def test_partial_tag_across_tokens_not_leaked():
    # "<thin" 片段不得作为文本发出；完整标签到达后正常切换
    p = TagStreamParser()
    events = p.feed("<thin")
    assert _text_of(events, "answer") == ""     # 旧实现会泄漏 "<th"
    events = p.feed("k>思考
")
    assert _text_of(events, "thinking") == "思考"


def test_partial_close_tag_across_tokens_not_leaked():
    p = TagStreamParser()
    p.feed("
")
    events = p.feed("内容</thin")
    assert "</thin" not in _text_of(events, "thinking")   # 旧实现（保留 2 字符）会泄漏
    events = p.feed("k>答案")
    assert _text_of(events, "thinking") == "内容"
    assert _text_of(events, "answer") == "答案"


def test_display_matches_persisted():
    p, events = _run(["一段很长的纯文本回答，没有任何标签，" * 5])
    assert _text_of(events, "answer") == p.answer_text    # 展示与持久化同源


def test_flush_emits_pending_normal_buffer():
    p = TagStreamParser()
    events = p.feed("尾部短")
    events += p.flush()
    assert _text_of(events, "answer") == "尾部短"


def test_flush_in_think_keeps_thinking():
    p = TagStreamParser()
    events = p.feed("
未闭合的思考")
    events += p.flush()
    assert _text_of(events, "thinking") == "未闭合的思考"
    assert _text_of(events, "answer") == ""


def test_empty_token():
    p = TagStreamParser()
    assert p.feed("") == []


def test_lone_lt_at_end_is_emitted_on_flush():
    p = TagStreamParser()
    events = p.feed("价格 <")
    events += p.flush()
    assert _text_of(events, "answer") == "价格 <"


def test_text_before_think_is_answer():
    p, events = _run(["前言文本", "
", "思考", "
", "答案"])
    assert "前言文本" in _text_of(events, "answer")
```

- [ ] **Step 2: 运行确认失败（模块不存在）**

- [ ] **Step 3: 实现 `app/core/tag_parser.py`**

```python
"""LLM 流式输出的 think/answer 标签解析器——纯逻辑，可离线单测。

feed(token) 返回事件列表 [{"kind": "answer"|"thinking", "text": str}]；
展示（SSE 发送）与持久化（answer_text/thinking_text）同源于事件流，
结构性消除旧内联状态机的"展示 3 字符未存库"类不一致。
"""

_NORMAL = 0
_IN_THINK = 1
_AFTER_THINK = 2

# 特殊标记，按状态分组
_MARKS = {
    _NORMAL: ("
</think>

` 或 `<answer>` 前缀的最长长度）


class TagStreamParser:
    def __init__(self) -> None:
        self._state = _NORMAL
        self._buf = ""
        self._answer: list[str] = []
        self._thinking: list[str] = []

    @property
    def answer_text(self) -> str:
        return "".join(self._answer)

    @property
    def thinking_text(self) -> str:
        return "".join(self._thinking)

    def feed(self, token: str) -> list[dict]:
        if not token:
            return []
        self._buf += token
        events: list[dict] = []
        while self._buf:
            marks = _MARKS[self._state]
            idx, mark = self._earliest(self._buf, marks)
            if idx >= 0:
                # 标记前的文本按安全窗口发出
                self._emit_safe(events, self._buf[:idx])
                self._buf = self._buf[idx:]
                if not self._transition(events, mark):
                    break  # 标记尚不完整（feed 结束），等下个 token
                continue
            # 无标记：发出安全前缀，保留可能含标签前缀的尾部
            self._emit_safe(events, self._buf)
            self._buf = self._safe_tail(self._buf)
            break
        return events

    def flush(self) -> list[dict]:
        """流结束：缓冲按当前状态全量发出。"""
        events: list[dict] = []
        if self._buf:
            kind = "thinking" if self._state == _IN_THINK else "answer"
            self._record(events, kind, self._buf)
            self._buf = ""
        return events

    # -- 内部 ----------------------------------------------------------

    @staticmethod
    def _earliest(buf: str, marks: tuple[str, ...]) -> tuple[int, str]:
        best_idx, best_mark = -1, ""
        for mark in marks:
            i = buf.find(mark[0]) if len(mark) == 1 else buf.find(mark[: _SAFE_KEEP + 1])
            # 先找完整标记
            i = buf.find(mark)
            if i >= 0 and (best_idx < 0 or i < best_idx):
                best_idx, best_mark = i, mark
        if best_idx >= 0:
            return best_idx, best_mark
        # 再找不完整的标记前缀（尾部）
        for mark in marks:
            for k in range(min(len(mark) - 1, len(buf)), 0, -1):
                if buf.endswith(mark[:k]):
                    return len(buf) - k, mark[:k]   # 不完整标记：截断位置
        return -1, ""

    def _transition(self, events: list[dict], mark: str) -> bool:
        """处理完整标记。返回 False 表示标记不完整、需等待更多 token。"""
        full = {m for marks in _MARKS.values() for m in marks}
        if mark not in full:
            return False
        if mark == "
</think>

":
            self._state = _IN_THINK
        elif mark == "
</think>

":
            self._state = _AFTER_THINK
        elif mark == "<answer>":
            self._state = _AFTER_THINK
        elif mark == "</answer>":
            pass  # 闭标签不改变状态，仅消费
        self._buf = self._buf[len(mark):]
        return True

    def _emit_safe(self, events: list[dict], text: str) -> None:
        """发出 text 中的安全前缀（末尾 _SAFE_KEEP 字符可能含未闭合标签，不发）。"""
        if not text:
            return
        if len(text) <= _SAFE_KEEP:
            return  # 全部留待后续/flush
        cut = len(text) - _SAFE_KEEP
        # 切点之后若含 '<'，回退到 '<' 之前
        tail = text[cut:]
        lt = tail.find("<")
        if lt >= 0:
            cut += lt
        if cut <= 0:
            return
        kind = "thinking" if self._state == _IN_THINK else "answer"
        self._record(events, kind, text[:cut])

    @staticmethod
    def _safe_tail(buf: str) -> str:
        """保留缓冲中可能成为标签前缀的尾部。"""
        if len(buf) <= _SAFE_KEEP:
            return buf
        tail = buf[-_SAFE_KEEP:]
        lt = tail.rfind("<")
        if lt >= 0:
            return tail[lt:]
        return ""

    def _record(self, events: list[dict], kind: str, text: str) -> None:
        if not text:
            return
        events.append({"kind": kind, "text": text})
        if kind == "answer":
            self._answer.append(text)
        else:
            self._thinking.append(text)
```

- [ ] **Step 4: 运行确认通过**

Run: `D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_tag_parser.py -v`
Expected: 12 passed。

- [ ] **Step 5: Commit**

```bash
git add app/core/tag_parser.py tests/unit/test_tag_parser.py
git commit -m "feat(stream): extract TagStreamParser pure class with think/answer tag handling + plan: tag-stream-parser"
```

---

### Task 2: pipeline 接入解析器

**Files:** `app/core/pipeline.py`

- [ ] **Step 1: 替换内联状态机**

导入 `from app.core.tag_parser import TagStreamParser`；在流式循环前初始化：

```python
        parser = TagStreamParser()
```

循环体替换为：

```python
            async for raw_token in minimax_client.chat_stream(
                messages,
                temperature=req.temperature,
                top_p=req.top_p,
            ):
                if first_token:
                    if ctx:
                        ctx.record("stream", first_token_ms=round((time.monotonic() - stream_start) * 1000, 1))
                    first_token = False
                full_buffer += raw_token
                for evt in parser.feed(raw_token):
                    text = evt["text"]
                    if not text:
                        continue
                    if evt["kind"] == "thinking":
                        yield f"event: thinking\ndata: {_sse_safe(text)}\n\n"
                    else:
                        yield f"event: token\ndata: {_sse_safe(_norm(text))}\n\n"
```

正常完成段（`# Normal completion` 注释起）替换为：

```python
        # Normal completion — 排空解析器缓冲
        for evt in parser.flush():
            text = evt["text"]
            if not text:
                continue
            if evt["kind"] == "thinking":
                yield f"event: thinking\ndata: {_sse_safe(text)}\n\n"
            else:
                yield f"event: token\ndata: {_sse_safe(_norm(text))}\n\n"
        answer_text = _norm(parser.answer_text)
        thinking_text = _norm(parser.thinking_text)
```

异常分支（GeneratorExit / Exception）中的持久化改用 `parser.answer_text` / `parser.thinking_text`：

```python
            if parser.answer_text or parser.thinking_text:
                await conversation_memory.add_message(
                    conv_id, "assistant",
                    _pii_safe(_norm(parser.answer_text)),
                    thinking_content=_pii_safe(_norm(parser.thinking_text)) if parser.thinking_text else None,
                    status="interrupted",
                    user_id=user_id,
                )
```

删除旧的 `_STATE_*`、`tag_state`、`tag_buffer`、`full_buffer` 之外的状态变量与三段分支代码。

- [ ] **Step 2: 验证 import 链 + 全量套件**

Run:
```bash
D:/miniConda/envs/rag/python.exe -c "import app.main"
D:/miniConda/envs/rag/python.exe -m pytest -q
```
Expected: `96 passed, 1 xfailed, 2 skipped`（84 + 12 parser 用例）。

- [ ] **Step 3: Commit**

```bash
git add app/core/pipeline.py
git commit -m "refactor(pipeline): stream via TagStreamParser — answer tags stripped, no partial-tag leaks"
```

---

### Task 3: 全量回归 + 收尾

- [ ] **Step 1: 手工冒烟（可选，需真实 key）**——启动 backend 发一个复杂问题（触发 `
</think>

`+`<answer>`），确认前端不再出现 `<answer>` 字面文本。

- [ ] **Step 2: 更新 plan 状态与索引，Commit**

```bash
git add docs/plans/
git commit -m "docs(plans): mark tag-stream-parser complete + plan: tag-stream-parser"
```

## Verification

| 验证项 | 期望 |
|---|---|
| parser 单测 | 12 passed |
| 全量套件 | `96 passed, 1 xfailed, 2 skipped` |
| import 链 | 退出码 0 |
| `<answer>` 不泄漏 | `test_answer_tags_are_stripped` / 冒烟 |
| 跨 token 不泄漏 | `test_partial_tag_across_tokens_not_leaked` / `test_partial_close_tag_across_tokens_not_leaked` |
| 展示=持久化 | `test_display_matches_persisted` |

## Explicitly NOT doing

| 不做 | 原因 |
|---|---|
| pipeline 级 SSE 端到端测试（fake chat_stream） | 需要 ASGI 级测试基建；parser 纯测已覆盖解析逻辑，pipeline 集成靠冒烟 |
| 思考内容的结构化（分步 JSON） | 产品形态未定，YAGNI |
| 前端 SSE 解析改动 | 后端已剥离标签，前端现有 thinking/token 事件消费不变 |

"""LLM 流式输出的 think/answer 标签解析器——纯逻辑，可离线单测。

feed(token) 返回事件列表 [{"kind": "answer"|"thinking", "text": str}]；
展示（SSE 发送）与持久化（answer_text / thinking_text）同源于事件流，
结构性消除旧内联状态机"展示了却没存库"类不一致。

缓冲策略：任何时刻保留"构成某标记前缀的最长后缀"，其余立即发出——
跨 token 边界的未闭合标签片段永不泄漏为文本。
"""

_NORMAL = 0
_IN_THINK = 1
_AFTER_THINK = 2

_LF = chr(10)
# system prompt 规定的标签形态：前置 空格+换行（简单回答允许裸 answer 开标签）
THINK_OPEN = " " + _LF + "<" + "think>"
THINK_CLOSE = " " + _LF + "<" + "/think>"
ANSWER_OPEN = "<" + "answer>"
ANSWER_CLOSE = "<" + "/answer>"

# 各状态下需要识别的完整标记
_MARKS = {
    _NORMAL: (THINK_OPEN, THINK_CLOSE, ANSWER_OPEN),
    _IN_THINK: (THINK_CLOSE,),
    _AFTER_THINK: (THINK_CLOSE, ANSWER_OPEN, ANSWER_CLOSE),
}
_ALL_MARKS = frozenset(m for ms in _MARKS.values() for m in ms)
_MAX_MARK_LEN = max(len(m) for m in _ALL_MARKS)


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
        return self._drain(final=False)

    def flush(self) -> list[dict]:
        """流结束：缓冲按当前状态全量发出（中断时思考内容不丢）。"""
        return self._drain(final=True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _drain(self, final: bool) -> list[dict]:
        events: list[dict] = []
        while self._buf:
            marks = _MARKS[self._state]
            found = [(self._buf.find(m), m) for m in marks]
            found = [(i, m) for i, m in found if i >= 0]
            if found:
                idx, mark = min(found, key=lambda x: x[0])
                before = self._buf[:idx]
                # 标记前的文本：通常可整段发出；若尾部恰好是某标记前缀则保留
                keep = self._partial_prefix_len(before)
                if keep:
                    self._emit(events, before[:-keep])
                    self._buf = before[-keep:] + self._buf[idx:]
                    continue
                self._emit(events, before)
                self._buf = self._buf[idx + len(mark):]
                self._apply(mark)
                continue

            # 无完整标记
            if final:
                self._emit(events, self._buf)
                self._buf = ""
                break
            keep = self._partial_prefix_len(self._buf)
            if keep == 0:
                # 无标记前缀风险时，仍保留末 _MAX_MARK_LEN-1 字符——
                # 下个 token 可能补出完整标记
                keep = min(len(self._buf), _MAX_MARK_LEN - 1)
            cut = len(self._buf) - keep
            if cut > 0:
                self._emit(events, self._buf[:cut])
                self._buf = self._buf[cut:]
            break
        return events

    @staticmethod
    def _partial_prefix_len(s: str) -> int:
        """s 的最长后缀中，构成任意标记（所有状态）真前缀的长度。"""
        best = 0
        for m in _ALL_MARKS:
            limit = min(len(m) - 1, len(s))
            for k in range(limit, 0, -1):
                if s.endswith(m[:k]):
                    if k > best:
                        best = k
                    break
        return best

    def _apply(self, mark: str) -> None:
        if mark == THINK_OPEN:
            self._state = _IN_THINK
        elif mark == THINK_CLOSE:
            self._state = _AFTER_THINK
        elif mark == ANSWER_OPEN:
            self._state = _AFTER_THINK
        # ANSWER_CLOSE：仅消费，不改变状态

    def _emit(self, events: list[dict], text: str) -> None:
        if not text:
            return
        kind = "thinking" if self._state == _IN_THINK else "answer"
        events.append({"kind": kind, "text": text})
        if kind == "answer":
            self._answer.append(text)
        else:
            self._thinking.append(text)

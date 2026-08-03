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
                # 完整标记就是边界：标记前的文本可整段发出。
                # （旧实现若 before 尾部恰为某标记前缀会回退重组 buf 后 continue，
                #   但标记位置不变 → 零进展死循环，曾把事件循环 100% 卡死。
                #   before 内不可能藏更靠前的未闭合片段——它不含任何完整标记，
                #   而其尾部前缀与 idx 处的完整标记重叠，已被该标记覆盖。）
                self._emit(events, self._buf[:idx])
                self._buf = self._buf[idx + len(mark):]
                self._apply(mark)
                continue

            # 无完整标记
            if final:
                self._emit(events, self._buf)
                self._buf = ""
                break
            # 只保留真正的标记前缀。旧实现额外保留任意末尾 9 字符，
            # 会把独立空白吞进缓冲、延迟正文输出（标签为规定形态，
            # 模型不会输出裸的 空格+换行 序列，无需泛保留）。
            keep = self._partial_prefix_len(self._buf)
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

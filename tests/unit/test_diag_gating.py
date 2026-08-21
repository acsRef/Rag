"""锁定 app/api/chat.py::stream_chat：仅当 settings.diagnostics_enabled 才创建 DiagContext。

设计审查 P0-1：diagnostics_enabled 之前是死的——chat 路径无条件创建 DiagContext，
关闭开关也照写遥测文件。此处验证开关真正生效。
"""

import app.api.chat as chat_mod


class _Recorder:
    """DiagContext 替身：记录实例化次数，不做任何文件写入。"""

    calls: int = 0

    def __init__(self, query: str = "") -> None:
        _Recorder.calls += 1
        self.query = query


def test_no_diagcontext_when_disabled(monkeypatch):
    _Recorder.calls = 0
    monkeypatch.setattr(chat_mod, "DiagContext", _Recorder)
    monkeypatch.setattr(chat_mod.settings, "diagnostics_enabled", False)
    assert chat_mod._build_diag_ctx("你好") is None
    assert _Recorder.calls == 0


def test_diagcontext_created_when_enabled(monkeypatch):
    _Recorder.calls = 0
    monkeypatch.setattr(chat_mod, "DiagContext", _Recorder)
    monkeypatch.setattr(chat_mod.settings, "diagnostics_enabled", True)
    ctx = chat_mod._build_diag_ctx("你好")
    assert isinstance(ctx, _Recorder)
    assert ctx.query == "你好"
    assert _Recorder.calls == 1

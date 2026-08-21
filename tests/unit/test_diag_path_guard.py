"""锁定 app/api/diagnostics.py::_is_safe_id：诊断文件 id 的字符集白名单。

diag_detail 的 `{diag_id:path}` 转换器允许斜杠，未校验时可拼出
`../` 读目录外的 .json 文件。白名单：字母数字开头，仅含字母数字与连字符。
"""

from app.api.diagnostics import _is_safe_id


def test_valid_ids_accepted():
    assert _is_safe_id("153012-abc123") is True
    assert _is_safe_id("a1b2c3d4e5f60718") is True
    assert _is_safe_id("000000-deadbe") is True


def test_traversal_rejected():
    assert _is_safe_id("../../etc/passwd") is False
    assert _is_safe_id("../index") is False
    assert _is_safe_id("a/b") is False
    assert _is_safe_id("..") is False


def test_empty_and_overlong_rejected():
    assert _is_safe_id("") is False
    assert _is_safe_id("a" * 200) is False


def test_special_chars_rejected():
    assert _is_safe_id("id.json") is False  # 双扩展名注入
    assert _is_safe_id("id\0") is False
    assert _is_safe_id("id\\x") is False
    assert _is_safe_id("-abc") is False  # 连字符不得开头

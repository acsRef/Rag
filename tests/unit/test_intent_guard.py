"""锁定 app/core/intent.py::_normalize_matches：畸形输出容忍 + 名称→id 归一。

背景：意图 prompt 允许 LLM 返回「知识库ID或名称」（示例全用名称），
旧实现直接把返回值当 kb_id 塞 SQL——返回名称时 0 命中且高分令兜底不触发；
缺键条目还在 try/except 之外构造 IntentMatch，KeyError 直接打死请求。
"""
from app.core.intent import _normalize_matches

KB_IDS = ["kb-001", "kb-002"]
KB_NAMES = {"kb-001": "产品文档", "kb-002": "运维手册"}


def test_valid_ids_pass_through():
    out = _normalize_matches(
        [{"kb_id": "kb-001", "score": 0.9}], KB_IDS, KB_NAMES)
    assert [(m.kb_id, m.score) for m in out] == [("kb-001", 0.9)]


def test_names_resolve_to_ids():
    out = _normalize_matches(
        [{"kb_id": "运维手册", "score": 0.8}], KB_IDS, KB_NAMES)
    assert [m.kb_id for m in out] == ["kb-002"]


def test_unknown_kb_dropped():
    assert _normalize_matches(
        [{"kb_id": "不存在的库", "score": 0.9}], KB_IDS, KB_NAMES) == []


def test_missing_key_dropped():
    assert _normalize_matches([{"score": 0.9}], KB_IDS, KB_NAMES) == []
    assert _normalize_matches([{"kb_id": "kb-001"}], KB_IDS, KB_NAMES) == []


def test_non_dict_entries_dropped():
    assert _normalize_matches(
        ["kb-001", 42, None, {"kb_id": "kb-001", "score": 0.5}],
        KB_IDS, KB_NAMES,
    ) and [m.kb_id for m in _normalize_matches(
        ["kb-001", 42, None, {"kb_id": "kb-001", "score": 0.5}],
        KB_IDS, KB_NAMES)] == ["kb-001"]


def test_bad_score_dropped():
    assert _normalize_matches(
        [{"kb_id": "kb-001", "score": "很高"}], KB_IDS, KB_NAMES) == []


def test_numeric_string_score_coerced():
    out = _normalize_matches(
        [{"kb_id": "kb-001", "score": "0.85"}], KB_IDS, KB_NAMES)
    assert out and out[0].score == 0.85


def test_non_list_raw_returns_empty():
    assert _normalize_matches(None, KB_IDS, KB_NAMES) == []
    assert _normalize_matches("garbage", KB_IDS, KB_NAMES) == []
    assert _normalize_matches({"kb_id": "kb-001"}, KB_IDS, KB_NAMES) == []


def test_whitespace_id_normalized():
    out = _normalize_matches(
        [{"kb_id": "  kb-002 ", "score": 0.7}], KB_IDS, KB_NAMES)
    assert [m.kb_id for m in out] == ["kb-002"]

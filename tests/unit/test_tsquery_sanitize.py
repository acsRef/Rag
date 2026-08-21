"""锁定 pgvector_store.tokenize 的 tsquery 安全性。

jieba 分词结果直接拼进 to_tsquery 时，`+ & | ! ( ) : ' " \\` 等运算符字符
会让 SQL 抛语法错误——异常被 _search_kb 吞掉后 BM25 通道静默消失
（如查询 "C++"）。tokenize 输出必须只含安全词元。
"""

from app.store.pgvector_store import tokenize


def test_plus_operators_stripped():
    out = tokenize("C++ 是什么", stopwords=False)
    assert "+" not in out
    assert "C" in out


def test_tsquery_operator_chars_stripped():
    out = tokenize("a|b & c!d (e) f:g 'h' \"i\"", stopwords=False)
    for ch in "|&!():'\"\\":
        assert ch not in out


def test_cjk_preserved():
    out = tokenize("绿色闪烁", stopwords=False)
    assert "绿色" in out and "闪烁" in out


def test_operator_only_tokens_dropped():
    out = tokenize("+++ --- ???", stopwords=False)
    # 纯运算符 token 必须整体丢弃，不得留下裸运算符进 tsquery
    assert all(tok.strip("-") for tok in out.split()) or out.strip() == ""
    for ch in "+?":
        assert ch not in out


def test_hyphen_inside_token_preserved():
    # 消毒函数保留 token 内部连字符（jieba 如何切词不在消毒职责内）
    from app.store.pgvector_store import _sanitize_ts_token

    assert _sanitize_ts_token("state-of-the-art") == "state-of-the-art"
    assert _sanitize_ts_token("RAG-2026") == "RAG-2026"
    assert _sanitize_ts_token("-") == ""  # 裸连字符丢弃
    assert _sanitize_ts_token("+++") == ""  # 纯符号丢弃

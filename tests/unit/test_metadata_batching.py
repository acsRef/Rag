"""ChunkMetadataGenerator.generate 批处理改造锁定测试（TDD 先行）。

生产事故根因：整文档单次 LLM 调用——2023 年报超时把 436 块问题清零
（更早一轮还静默丢过 41 块）；2024/2025 截断 JSON 只覆盖 12/50 块，
全库 ~1% 问题覆盖率。本文件锁定三层修复：

1. 分批调用（_BATCH_SIZE 块/批）：单批爆炸半径有界、响应 JSON 变小；
2. 批级重试（共 3 次尝试，覆盖超时/限流/空响应/JSON 解析失败）；
3. 失败批优雅降级为空元数据——绝不上抛、绝不丢块。

补强契约（终审意见）：LLM 响应必须逐条回显批内序号（"index"），解析
严格按回显值匹配；缺失/非法/越界一律视为「未命中」（对应 chunk 空
元数据），禁止按列表位置对位。

补丁点：monkeypatch ``app.ingestion.metadata.call_llm_with_retry``
（本模块唯一的 LLM 调用入口，generate 内部经 _call_llm_once 引用它）
+ ``_backoff_sleep``（测试提速，不真睡）。离线纯 fake，不触网络。
"""

import json
import logging
import re

from app.ingestion import metadata as md_mod
from app.ingestion.chunker import Chunk
from app.llm.base import PermanentError, TemporaryError

GEN = md_mod.chunk_metadata_generator


# ── 测试脚手架 ────────────────────────────────────────────────────────────


def _mk_chunks(n):
    return [Chunk(text=f"第{i}块正文", section_path=["路径"]) for i in range(n)]


def _entry(i, tag="t"):
    return {
        "index": i,
        "title": f"{tag}{i}标题",
        "summary": f"{tag}{i}摘要",
        "questions": [f"{tag}{i}问题？"],
    }


def _full_batch_resp(size, tag="t"):
    return json.dumps({"chunks": [_entry(i, tag) for i in range(size)]}, ensure_ascii=False)


class _ScriptedLLM:
    """按脚本次序返回响应或抛异常；记录每次调用的 prompt。"""

    def __init__(self, script):
        self.script = list(script)  # 每项：str（响应文本）或 Exception 实例
        self.prompts = []

    async def __call__(self, chat_fn, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _install(monkeypatch, script):
    fake = _ScriptedLLM(script)
    sleeps = []
    monkeypatch.setattr(md_mod, "call_llm_with_retry", fake)
    monkeypatch.setattr(md_mod, "_backoff_sleep", lambda a: sleeps.append(a), raising=False)
    return fake, sleeps


# ── 用例 ──────────────────────────────────────────────────────────────────


def test_generate_calls_llm_once_per_batch(monkeypatch):
    """60 块 × 25/批 → 恰 3 次调用；每次提示的批内序号 ≤24；跨批映射不错位。"""
    chunks = _mk_chunks(60)
    fake, _ = _install(
        monkeypatch,
        [_full_batch_resp(25, "A"), _full_batch_resp(25, "B"), _full_batch_resp(10, "C")],
    )

    result = GEN.generate(chunks)

    assert len(fake.prompts) == 3, "60 块必须拆成 3 批（25+25+10），每批一次调用"
    for p in fake.prompts:
        markers = [int(m) for m in re.findall(r"【(\d+)】", p)]
        assert max(markers) <= 24, "每批提示里的批内序号不得越过 24"
    assert result is chunks
    # 全局映射：批 0 → 0..24，批 1 → 25..49，批 2 → 50..59
    assert chunks[0].title == "A0标题"
    assert chunks[24].title == "A24标题"
    assert chunks[25].title == "B0标题"
    assert chunks[49].title == "B24标题"
    assert chunks[50].title == "C0标题"
    assert chunks[59].title == "C9标题"
    assert all(c.questions for c in chunks)


def test_batch_failure_degrades_gracefully(monkeypatch, caplog):
    """30 块两批：批 1 三次全败 → 仅批 1 降级为空元数据；块一个不少。"""
    chunks = _mk_chunks(30)
    fake, _ = _install(
        monkeypatch,
        [_full_batch_resp(25)] + [TemporaryError("模拟超时")] * 3,
    )

    with caplog.at_level(logging.WARNING, logger="app.ingestion.metadata"):
        result = GEN.generate(chunks)  # 不得上抛

    assert result is chunks
    assert len(result) == 30, "失败批的块必须保留，不得丢弃"
    assert chunks[0].title == "t0标题"
    assert chunks[24].questions
    assert chunks[25].title == "" and chunks[25].questions == []
    assert chunks[29].summary == ""
    assert len(fake.prompts) == 4, "批 0 一次 + 批 1 三次（1 首试 + 2 重试）"
    assert "ingest.metadata_batch_failed" in caplog.text
    assert "attempt=3" in caplog.text


def test_batch_retry_then_success(monkeypatch):
    """首试抛临时错误、二试成功 → 批正常填充，恰 2 次尝试、中间退避 1 次。"""
    chunks = _mk_chunks(10)
    fake, sleeps = _install(monkeypatch, [TemporaryError("模拟限流"), _full_batch_resp(10)])

    result = GEN.generate(chunks)

    assert len(fake.prompts) == 2
    assert sleeps == [0], "两次尝试之间应退避一次"
    assert result is chunks
    assert chunks[7].title == "t7标题"
    assert chunks[7].questions == ["t7问题？"]


def test_partial_json_only_populates_matched_chunks(monkeypatch):
    """JSON 只覆盖部分块：命中的填充，未命中的保持空——部分覆盖不算失败，不重试。"""
    chunks = _mk_chunks(10)
    resp = json.dumps({"chunks": [_entry(1), _entry(4), _entry(9)]}, ensure_ascii=False)
    fake, _ = _install(monkeypatch, [resp])

    result = GEN.generate(chunks)

    assert len(fake.prompts) == 1, "部分覆盖是可接受结果，不得触发重试"
    assert result is chunks and len(result) == 10
    assert chunks[1].title == "t1标题"
    assert chunks[4].title == "t4标题"
    assert chunks[9].title == "t9标题"
    for i in (0, 2, 3, 5, 6, 7, 8):
        assert chunks[i].title == "" and chunks[i].questions == []


def test_single_chunk_document_still_works(monkeypatch):
    """边界：单块文档一次调用即完成；空输入原样返回且零调用。"""
    chunks = _mk_chunks(1)
    fake, _ = _install(monkeypatch, [_full_batch_resp(1)])

    result = GEN.generate(chunks)

    assert len(fake.prompts) == 1
    assert result is chunks
    assert chunks[0].title == "t0标题"

    assert GEN.generate([]) == []
    assert len(fake.prompts) == 1


def test_missing_middle_entry_no_positional_shift(monkeypatch):
    """批内 5 块，响应跳过第 2 条：0/1/3/4 各填自己那块，第 2 块空，禁止串位。"""
    words = ["甲", "乙", "丙", "丁", "戊"]
    chunks = [Chunk(text=f"包含特征词{w}的正文", section_path=["路径"]) for w in words]
    resp = json.dumps(
        {"chunks": [_entry(0, "甲"), _entry(1, "乙"), _entry(3, "丁"), _entry(4, "戊")]},
        ensure_ascii=False,
    )
    fake, _ = _install(monkeypatch, [resp])

    result = GEN.generate(chunks)

    assert len(fake.prompts) == 1
    assert result is chunks and len(result) == 5
    # 命中的四块各自填的是「自己那块」的内容（title 带各自特征词标签）
    assert chunks[0].title == "甲0标题"
    assert chunks[1].title == "乙1标题"
    assert chunks[3].title == "丁3标题"
    assert chunks[4].title == "戊4标题"
    # 缺位的第 2 块空元数据；相邻块不得串位（若按列表位置 zip，丁会落进块 2）
    assert chunks[2].title == ""
    assert chunks[2].summary == ""
    assert chunks[2].questions == []


def test_malformed_json_retried_then_degraded(monkeypatch, caplog):
    """JSON 解析失败属临时故障：重试满 3 次后降级该批，不上抛、不丢块。"""
    chunks = _mk_chunks(5)
    bad = "前置说明 {oops 这不是JSON"
    fake, sleeps = _install(monkeypatch, [bad, bad, bad])

    with caplog.at_level(logging.WARNING, logger="app.ingestion.metadata"):
        result = GEN.generate(chunks)

    assert len(fake.prompts) == 3
    assert sleeps == [0, 1]
    assert result is chunks and len(result) == 5
    assert all(c.questions == [] for c in chunks)
    assert "ingest.metadata_batch_failed" in caplog.text


def test_apply_response_crash_still_degrades(monkeypatch):
    """_apply_response 内部炸（病态深嵌套 JSON 触发 RecursionError）也不得击穿
    generate() 的「绝不上抛」保证：重试满后全批空元数据、块一个不少。

    回归背景（spec review on eeff54f）：_apply_response 曾在 try/except 的
    else 分支里，解析异常逃过 except 直接上抛。注入点用真实 robust_json_parse
    对深嵌套输入必现 RecursionError，不 mock 解析函数本身。
    """
    deep_nesting = "[" * 5000 + "]" * 5000  # robust_json_parse 必现 RecursionError
    chunks = _mk_chunks(5)
    fake, sleeps = _install(monkeypatch, [deep_nesting] * 3)

    result = GEN.generate(chunks)  # 修复前此处直接 RecursionError 上抛

    assert len(fake.prompts) == 3, "解析崩溃按可重试故障处理：满 3 次尝试"
    assert sleeps == [0, 1]
    assert result is chunks and len(result) == 5, "块一个不少"
    for c in chunks:
        assert c.title == "" and c.summary == "" and c.questions == []


def test_permanent_error_degrades_without_retrying(monkeypatch, caplog):
    """永久错误（4xx/鉴权）不消耗重试预算：1 次调用即降级本批。

    锁定 metadata.py 的 PermanentError 短路契约：break 在退避之前，
    不烧剩余尝试、不退避，警告 attempt=1。
    """
    chunks = _mk_chunks(5)
    fake, sleeps = _install(monkeypatch, [PermanentError("模拟 401 鉴权失败")])

    with caplog.at_level(logging.WARNING, logger="app.ingestion.metadata"):
        result = GEN.generate(chunks)

    assert len(fake.prompts) == 1, "永久错误必须短路：仅 1 次调用，不得重试"
    assert sleeps == [], "break 在 _backoff_sleep 之前——永久错误路径零退避"
    assert result is chunks and len(result) == 5, "块一个不少"
    for c in chunks:
        assert c.title == "" and c.summary == "" and c.questions == []
    assert "ingest.metadata_batch_failed" in caplog.text
    assert "attempt=1" in caplog.text
    assert "permanent" in caplog.text


def test_doc_label_threads_into_batch_logs(monkeypatch, caplog):
    """doc_label 归属：ok/failed 两类批日志都带 doc= 字段；缺省时字段省略。"""
    # 失败批：3 次尝试全败 → failed 日志带 doc=
    chunks = _mk_chunks(10)
    fake, _ = _install(monkeypatch, [TemporaryError("模拟超时")] * 3)
    with caplog.at_level(logging.WARNING, logger="app.ingestion.metadata"):
        GEN.generate(chunks, doc_label="abc12345:年报.pdf")
    failed_rec = next(
        r for r in caplog.records if r.message.startswith("ingest.metadata_batch_failed")
    )
    assert "doc=abc12345:年报.pdf" in failed_rec.message

    # 成功批：ok 日志同样带 doc=（复用新 fake，避免 caplog 混入上一段）
    caplog.clear()
    chunks2 = _mk_chunks(10)
    fake2, _ = _install(monkeypatch, [_full_batch_resp(10)])
    with caplog.at_level(logging.INFO, logger="app.ingestion.metadata"):
        GEN.generate(chunks2, doc_label="def67890:手册.docx")
    ok_rec = next(r for r in caplog.records if r.message.startswith("ingest.metadata_batch_ok"))
    assert "doc=def67890:手册.docx" in ok_rec.message

    # 缺省（向后兼容）：不带 doc_label 调用 → 日志不含 doc= 字段
    caplog.clear()
    chunks3 = _mk_chunks(10)
    _install(monkeypatch, [TemporaryError("模拟超时")] * 3)
    with caplog.at_level(logging.WARNING, logger="app.ingestion.metadata"):
        GEN.generate(chunks3)
    failed_rec3 = next(
        r for r in caplog.records if r.message.startswith("ingest.metadata_batch_failed")
    )
    assert "doc=" not in failed_rec3.message

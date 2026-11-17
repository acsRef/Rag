"""`INGEST_METADATA_ENABLED=0` 的入库提速开关（2026-11-17）。

背景：元数据抽取向每个 25-chunk 批次发一次 LLM 请求，网络抖动时重试到分钟级；
重灌 3 份 470-chunk 年报要 1–2 小时。关掉它只做「分块 + embedding」。
默认开启，行为不变。
"""

from __future__ import annotations

from app.ingestion.metadata import _MetadataDisabled


def test_disabled_generator_returns_chunks_untouched(monkeypatch):
    chunks = [object(), object()]
    out = _MetadataDisabled().generate(chunks, doc_label="x")
    assert out is chunks, "关闭元数据时必须原样返回，不能改 chunk 内容"

"""设计审查 P0-5：索引器并行 future 的成对收尾。

旧实现 `_meta_fut.result()` 抛异常时 `_embed_fut` 不被 await/cancel，embedding
线程悬空、异常静默丢失。`_collect_future_pair` 保证任一失败时取消并吞掉另一个。
"""
import concurrent.futures
import time

import pytest

from app.ingestion.indexer import _collect_future_pair


def test_success_path_returns_both_results():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        a = ex.submit(lambda: "meta")
        b = ex.submit(lambda: "embed")
        assert _collect_future_pair(a, b) == ("meta", "embed")


def test_failure_propagates_and_peer_is_drained():
    """meta 失败时异常向上抛，embed future 不再悬空（被 cancel 或已 consume）。"""

    def raise_meta():
        raise ValueError("meta boom")

    def slow_but_finite():
        time.sleep(0.2)
        return "embed-ok"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        meta = ex.submit(raise_meta)
        embed = ex.submit(slow_but_finite)
        with pytest.raises(ValueError, match="meta boom"):
            _collect_future_pair(meta, embed)
        # 异常路径下 embed 必须被收尾：要么已取消，要么已跑完（done），不悬空
        assert embed.cancelled() or embed.done()
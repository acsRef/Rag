"""reembed_v2.py — 批量重 embed 工具（Day 2 上午 + 反思后修订）。

两种模式：
- 默认（生产）：用 `c.text` 喂 embedding，标 embedding_version=1
- `--use-build-embedding-text`：用 `app.ingestion.embedding_text.build_embedding_text()` 加
  document/section prefix，标 embedding_version=2（实验性；ablation 实测负收益，保留供未来 A/B）

历史：
- Day 2 上午首次实现只支持 v2（build_embedding_text + version=2）
- baseline 验证发现 v2 让 recall@10 1.000 → 0.984 / MRR 0.876 → 0.824
- 回滚到 chunk-only embedding（version=1），本工具加 flag 支持任意目标

设计要点：
- 增量：只处理 `embedding_version != target_version` 的 chunks（重跑安全）
- 批量：sf_embedding.embed_with_fallback 内部按 32 分片；脚本侧按 256 chunk 一批
- 失败容错：单 chunk 失败 → log + skip，不中断整批

用法：
    # 生产（默认）：用 c.text 重建 v1 embedding
    D:/miniConda/envs/rag/python.exe tools/reembed_v2.py

    # 实验：用 build_embedding_text 重建 v2 embedding
    D:/miniConda/envs/rag/python.exe tools/reembed_v2.py --use-build-embedding-text --target-version 2

    # 限制 + dry-run
    D:/miniConda/envs/rag/python.exe tools/reembed_v2.py --limit 50 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

# 让脚本能 import app.*（项目根）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from app.llm.embedding import sf_embedding
from app.store.db import Chunk, Document, get_db_ctx


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reembed")


# 单批上限：sf_embedding 内部按 32 分批，脚本侧按 256 chunk 一批以兼顾 DB 内存
BATCH_SIZE = 256


def _load_chunks(limit: int | None, target_version: int):
    """加载所有 embedding_version != target_version 的 chunks。"""
    with get_db_ctx() as session:
        stmt = (
            select(Chunk, Document.filename)
            .join(Document, Document.document_id == Chunk.document_id)
            .where(Chunk.embedding_version != target_version)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()
    return [
        (chunk.chunk_id, chunk, filename)
        for (chunk, filename) in rows
    ]


def _build_text(chunk, filename: str, use_build: bool) -> str:
    """构造 embedding 输入。use_build=True 走 build_embedding_text（实验），否则 c.text（生产）。"""
    if not use_build:
        return chunk.text or ""
    from types import SimpleNamespace
    from app.ingestion.embedding_text import build_embedding_text
    doc = SimpleNamespace(filename=filename)
    return build_embedding_text(chunk, doc)


async def _embed_batch(texts):
    """调 sf_embedding.embed_with_fallback；返回 (vec | None, err | None) 列表。"""
    return await sf_embedding.embed_with_fallback(texts)


def _persist_results(batch, results, target_version: int, use_build: bool, dry_run: bool):
    """把新 embedding + embedding_text + version 写回 DB。"""
    if dry_run:
        return 0
    written = 0
    with get_db_ctx() as session:
        for (cid, chunk, filename), result in zip(batch, results):
            vec, err = result
            if vec is None:
                logger.warning("  skip %s: %s", cid[:12], err or "no embedding")
                continue
            emb_text = _build_text(chunk, filename, use_build)
            session.execute(
                update(Chunk)
                .where(Chunk.chunk_id == cid)
                .values(
                    embedding=vec,
                    embedding_text=emb_text,
                    embedding_version=target_version,
                )
            )
            written += 1
        session.commit()
    return written


async def run(limit: int | None, target_version: int, use_build: bool, dry_run: bool):
    t0 = time.monotonic()
    all_rows = _load_chunks(limit, target_version)
    total = len(all_rows)
    if total == 0:
        logger.info(
            "No chunks need re-embedding (all at target version=%d). Done.",
            target_version,
        )
        return

    logger.info(
        "Found %d chunks to reembed (target_version=%d, use_build=%s, limit=%s, dry_run=%s)",
        total, target_version, use_build, limit, dry_run,
    )

    written = 0
    failed = 0
    for batch_start in range(0, total, BATCH_SIZE):
        batch = all_rows[batch_start : batch_start + BATCH_SIZE]
        inputs = [_build_text(c, fn, use_build) for (_cid, c, fn) in batch]

        non_empty_idx = [i for i, t in enumerate(inputs) if t]
        non_empty_inputs = [inputs[i] for i in non_empty_idx]

        if non_empty_inputs:
            results_full = await _embed_batch(non_empty_inputs)
        else:
            results_full = []

        # 把结果填回 batch 顺序；空文本位置用 (None, "empty text")
        results = [(None, "empty text")] * len(batch)
        for j, idx in enumerate(non_empty_idx):
            results[idx] = results_full[j] if j < len(results_full) else (None, "missing")

        # 写库
        if not dry_run:
            for (_cid, _c, _f), result in zip(batch, results):
                vec, err = result
                if vec is None:
                    failed += 1
            written += _persist_results(batch, results, target_version, use_build, dry_run)
        else:
            for (_c, _c2, _f), result in zip(batch, results):
                if result[0] is None:
                    failed += 1

        if (batch_start // BATCH_SIZE) % 4 == 0:
            elapsed = time.monotonic() - t0
            logger.info(
                "  progress %d/%d (%.1fs elapsed, written=%d failed=%d)",
                min(batch_start + BATCH_SIZE, total), total, elapsed, written, failed,
            )

    elapsed = time.monotonic() - t0
    logger.info(
        "Done. processed=%d written=%d failed=%d elapsed=%.1fs (%.1f chunks/s)",
        total, written, failed, elapsed, total / elapsed if elapsed > 0 else 0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="只处理前 N 条（smoke 用）")
    parser.add_argument("--target-version", type=int, default=1,
                        help="目标 embedding_version（默认 1 = 生产 chunk-only）")
    parser.add_argument("--use-build-embedding-text", action="store_true",
                        help="用 build_embedding_text() 加 document/section prefix（实验性）")
    parser.add_argument("--dry-run", action="store_true",
                        help="不写 DB，仅打印统计")
    args = parser.parse_args()

    asyncio.run(run(
        limit=args.limit,
        target_version=args.target_version,
        use_build=args.use_build_embedding_text,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()

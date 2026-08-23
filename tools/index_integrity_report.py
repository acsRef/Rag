"""Index Integrity Report —— 每次 re-index 后必跑的硬断言（spec §8）。

哲学：不允许"配置开了但数据没进来"（或反向）的静默错配再次发生。
历史上两次踩坑：question channel 配置开而 chunk_questions 空（历史语料早于功能）、
QUESTION_CHANNEL_ENABLED 只门控检索侧导致摄入侧无条件写库。本工具把这些
错配变成显式 FAIL。

用法：
    D:/miniConda/envs/rag/python.exe tools/index_integrity_report.py \
        --expect-documents 3 --expect-questions zero
    ... --expect-questions positive   # question channel 激活后
退出码非 0 = 有断言失败。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.store.db import engine  # noqa: E402


def vec_dim_of_sample(conn, sql: str) -> int | None:
    """取一行向量的维度；pgvector text 形如 '[0.1,0.2,...]'，逗号数+1 即维度。"""
    row = conn.execute(text(sql)).first()
    if not row or row[0] is None:
        return None
    return row[0].count(",") + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-documents", type=int, default=3)
    parser.add_argument("--expect-questions", choices=["zero", "positive"], required=True)
    args = parser.parse_args()

    failures: list[str] = []

    with engine.connect() as conn:

        def check(label: str, actual, ok: bool):
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {label}: {actual}")
            if not ok:
                failures.append(label)

        n_docs = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        check(f"documents == {args.expect_documents}", n_docs, n_docs == args.expect_documents)

        n_chunks = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
        check("chunks > 0", n_chunks, n_chunks > 0)

        n_null_emb = conn.execute(
            text("SELECT count(*) FROM chunks WHERE embedding IS NULL")
        ).scalar_one()
        check("chunks.embedding NULL == 0", n_null_emb, n_null_emb == 0)

        dim = vec_dim_of_sample(
            conn, "SELECT embedding::text FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
        )
        check(
            f"chunk 向量维度 == {settings.embedding_dimension}",
            dim,
            dim == settings.embedding_dimension,
        )

        # embedding_text：null 率 / 平均长度（表格归一化后应非空）
        n_et_null = conn.execute(
            text("SELECT count(*) FROM chunks WHERE embedding_text IS NULL OR embedding_text = ''")
        ).scalar_one()
        avg_et = conn.execute(
            text("SELECT COALESCE(avg(length(embedding_text)), 0)::int FROM chunks")
        ).scalar_one()
        check("embedding_text 空 ratio == 0", f"{n_et_null}/{n_chunks}", n_et_null == 0)
        print(f"[INFO] embedding_text 平均长度: {avg_et}")

        n_st_empty = conn.execute(
            text("SELECT count(*) FROM chunks WHERE search_text IS NULL OR search_text = ''")
        ).scalar_one()
        check("search_text 空 ratio == 0", f"{n_st_empty}/{n_chunks}", n_st_empty == 0)

        # chunk_type 是后加列（init_db ALTER IF NOT EXISTS）；旧库可能还没有 —— 只 INFO 不硬断
        has_chunk_type = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'chunks' AND column_name = 'chunk_type'"
            )
        ).scalar_one()
        if has_chunk_type:
            n_tables = conn.execute(
                text("SELECT count(*) FROM chunks WHERE chunk_type = 'table'")
            ).scalar_one()
            pct = 100 * n_tables / n_chunks if n_chunks else 0.0
            print(f"[INFO] table chunks: {n_tables} ({pct:.1f}%)")
        else:
            print("[INFO] table chunks: SKIP（chunks.chunk_type 列不存在——旧库未跑 init_db）")

        n_q = conn.execute(text("SELECT count(*) FROM chunk_questions")).scalar_one()
        if args.expect_questions == "zero":
            check("chunk_questions == 0（硬断言）", n_q, n_q == 0)
        else:
            check("chunk_questions > 0（硬断言）", n_q, n_q > 0)
            qdim = vec_dim_of_sample(
                conn,
                "SELECT embedding::text FROM chunk_questions WHERE embedding IS NOT NULL LIMIT 1",
            )
            check(
                f"问题向量维度 == {settings.embedding_dimension}",
                qdim,
                qdim == settings.embedding_dimension,
            )

    if failures:
        print(f"\nINTEGRITY FAILED: {len(failures)} 项 -> {failures}")
        return 1
    print("\nINTEGRITY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

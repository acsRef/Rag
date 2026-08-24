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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-documents", type=int, required=True)
    parser.add_argument("--expect-questions", choices=["zero", "positive"], required=True)
    args = parser.parse_args()

    failures: list[str] = []

    # READ_ONLY：把"本工具绝不写库"从事务层面强制住（写操作会直接报错）。
    # psycopg2 方言不认 isolation_level="READ_ONLY"，须用方言专属选项 postgresql_readonly
    with engine.connect().execution_options(postgresql_readonly=True) as conn:

        def check(label: str, actual, ok: bool):
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {label}: {actual}")
            if not ok:
                failures.append(label)

        # 核心表存在性探测（to_regclass，同 reset_rag_corpus.py）：目标库不对/init_db 未跑时干净报错
        for t in ("documents", "chunks", "chunk_questions"):
            present = conn.execute(text("SELECT to_regclass(:r) IS NOT NULL"), {"r": t}).scalar()
            if not present:
                print(f"ERROR: 核心表 {t} 不存在——目标库不对或 init_db 未跑")
                return 1

        n_docs = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        check(f"documents == {args.expect_documents}", n_docs, n_docs == args.expect_documents)

        n_chunks = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
        check("chunks > 0", n_chunks, n_chunks > 0)

        n_null_emb = conn.execute(
            text("SELECT count(*) FROM chunks WHERE embedding IS NULL")
        ).scalar_one()
        check("chunks.embedding NULL == 0", n_null_emb, n_null_emb == 0)

        # 穷举维度核查（vector_dims），不是 LIMIT 1 抽样——混合维度的库不能假 PASS
        n_wrong_dim = conn.execute(
            text(
                "SELECT count(*) FROM chunks "
                "WHERE embedding IS NOT NULL AND vector_dims(embedding) <> :dim"
            ),
            {"dim": settings.embedding_dimension},
        ).scalar_one()
        check(
            f"chunk 向量维度全部 == {settings.embedding_dimension}（穷举）",
            f"{n_wrong_dim} 行不符",
            n_wrong_dim == 0,
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

        # question 覆盖率（仅观测，不硬断言）：metadata 批处理降级后，
        # 失败批的问题为空但 chunk 保留——用覆盖率暴露降级的真实规模。
        n_qc = conn.execute(
            text("SELECT count(DISTINCT chunk_id) FROM chunk_questions")
        ).scalar_one()
        qc_pct = 100 * n_qc / n_chunks if n_chunks else 0.0
        print(f"[INFO] question 覆盖: {n_qc}/{n_chunks} chunks ({qc_pct:.1f}%)")
        doc_rows = conn.execute(
            text(
                "SELECT c.document_id, count(DISTINCT q.chunk_id) AS covered, "
                "count(c.chunk_id) AS total, max(d.filename) AS filename "
                "FROM chunks c LEFT JOIN chunk_questions q ON q.chunk_id = c.chunk_id "
                "GROUP BY c.document_id ORDER BY c.document_id"
            )
        ).fetchall()
        for r in doc_rows:
            pct = 100 * r.covered / r.total if r.total else 0.0
            print(f"[INFO]   doc={r.document_id[:8]} {r.filename}: {r.covered}/{r.total} ({pct:.1f}%)")

        n_q = conn.execute(text("SELECT count(*) FROM chunk_questions")).scalar_one()
        if args.expect_questions == "zero":
            check("chunk_questions == 0（硬断言）", n_q, n_q == 0)
        else:
            check("chunk_questions > 0（硬断言）", n_q, n_q > 0)
            n_q_null = conn.execute(
                text("SELECT count(*) FROM chunk_questions WHERE embedding IS NULL")
            ).scalar_one()
            check("chunk_questions.embedding NULL == 0", n_q_null, n_q_null == 0)
            n_q_wrong_dim = conn.execute(
                text(
                    "SELECT count(*) FROM chunk_questions "
                    "WHERE embedding IS NOT NULL AND vector_dims(embedding) <> :dim"
                ),
                {"dim": settings.embedding_dimension},
            ).scalar_one()
            check(
                f"问题向量维度全部 == {settings.embedding_dimension}（穷举）",
                f"{n_q_wrong_dim} 行不符",
                n_q_wrong_dim == 0,
            )

    if failures:
        print(f"\nINTEGRITY FAILED: {len(failures)} 项 -> {failures}")
        return 1
    print("\nINTEGRITY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

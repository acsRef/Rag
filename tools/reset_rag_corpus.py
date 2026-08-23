"""清空语料 + 会话数据 —— 数据销毁专用，与 schema 迁移(migrate_vector_dimension.py)分离。

单语句多表 TRUNCATE（免 CASCADE 且满足互引 FK）+ RESTART IDENTITY。
保留 users / roles / permissions / user_roles / knowledge_bases / kb_role_access /
sensitive_rules / pii_alerts / pii_hold / dim_* / fact_*。

用法：
    D:/miniConda/envs/rag/python.exe tools/reset_rag_corpus.py           # dry-run 打印将清对象与行数
    D:/miniConda/envs/rag/python.exe tools/reset_rag_corpus.py --yes     # 真正执行
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text  # noqa: E402

from app.store.db import engine  # noqa: E402  (create_engine 惰性，import 无连接副作用)

TRUNCATE_TABLES = (
    # 会话侧（messages←conversations FK）
    "messages",
    "conversations",
    # LangGraph 会话 checkpoint（当前为空表，一并纳入防遗留）
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    # 语料侧（chunks←documents/doc_role_access 互引，单语句多表处理）
    "chunk_questions",
    "chunks",
    "doc_role_access",
    "documents",
    "doc_embeddings",
    "doc_entities",
    "doc_relations",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="真正执行（默认 dry-run）")
    args = parser.parse_args()

    stmt = text(f"TRUNCATE TABLE {', '.join(TRUNCATE_TABLES)} RESTART IDENTITY")

    with engine.begin() as conn:
        print("将清空的表：")
        total = 0
        for t in TRUNCATE_TABLES:
            n = conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()
            total += n
            print(f"  {t}: {n} 行")
        print(f"合计 {total} 行")

        if not args.yes:
            print("\n(dry-run；加 --yes 执行 TRUNCATE)")
            return 0
        conn.execute(stmt)
        print("\nTRUNCATE 完成（RESTART IDENTITY）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

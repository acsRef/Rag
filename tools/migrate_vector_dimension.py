"""向量列维度迁移 —— 只管 schema，不管数据销毁（清库请用 reset_rag_corpus.py）。

对 chunks / chunk_questions / doc_embeddings 三表的 embedding 列逐一检测：
- 当前维度 == 目标 → skip
- 行数 > 0 → ABORT（要求先清库；空表 ALTER 才是瞬时安全的）
- 否则 ALTER TYPE vector(:target)

用法：
    D:/miniConda/envs/rag/python.exe tools/migrate_vector_dimension.py --target 1024 [--apply]

不带 --apply 时 dry-run，只打印检测结论。
维度检测用 format_type() 读显示串再解析，不依赖 pgvector typmod 编码细节。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.store.db import engine  # noqa: E402  (create_engine 惰性，import 无连接副作用)

TABLES = ("chunks", "chunk_questions", "doc_embeddings")


def parse_vector_type(type_str: str | None) -> int | None:
    """'vector(4096)' → 4096；非 vector 类型返回 None。"""
    if not type_str:
        return None
    m = re.fullmatch(r"\s*vector\s*\(\s*(\d+)\s*\)\s*", type_str)
    return int(m.group(1)) if m else None


def get_column_type(conn, table: str, column: str = "embedding") -> str | None:
    row = conn.execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a "
            "WHERE a.attrelid = (:t)::regclass AND a.attname = :c AND a.attnum > 0"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def get_row_count(conn, table: str) -> int:
    return conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", type=int, default=None, help="目标维度（默认取 settings.embedding_dimension）"
    )
    parser.add_argument("--apply", action="store_true", help="真正执行 ALTER（默认 dry-run）")
    args = parser.parse_args()

    target = args.target if args.target is not None else settings.embedding_dimension
    # pgvector typmod 上限 16000；越界会在 ALTER 时才报错，提前拦截
    if not 1 <= target <= 16000:
        print(f"ERROR: --target {target} 越界（合法范围 1..16000）")
        return 1
    fatal = False

    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            for table in TABLES:
                cur_type = get_column_type(conn, table)
                cur_dim = parse_vector_type(cur_type)
                rows = get_row_count(conn, table)
                status = "SKIP(已是目标维度)" if cur_dim == target else "ALTER"
                print(f"{table}: type={cur_type} rows={rows} -> {status}")

                if cur_dim is None:
                    print(f"  ABORT: {table}.embedding 不是 vector 类型（{cur_type}）")
                    fatal = True
                    continue
                if cur_dim == target:
                    continue
                if rows > 0:
                    print(f"  ABORT: {table} 有 {rows} 行——先跑 tools/reset_rag_corpus.py 清库")
                    fatal = True
                    continue
                if args.apply:
                    conn.execute(
                        text(f'ALTER TABLE "{table}" ALTER COLUMN embedding TYPE vector({target})')
                    )
                    print(f"  ALTERED -> vector({target})")
            if fatal:
                # begin() 块正常退出会提交——必须抛异常让整个事务回滚，
                # 避免"部分表已 ALTER、部分 ABORT"的混合维度状态被持久化
                raise RuntimeError("存在 ABORT 项——事务回滚，未执行任何 ALTER")
    except RuntimeError as e:
        print(f"\n{e}")
        return 1
    if not args.apply:
        print("\n(dry-run 完成；加 --apply 执行)")
    else:
        print(f"\n迁移完成：全部 embedding 列 -> vector({target})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

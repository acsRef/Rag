"""index_integrity_report 的离线单测（DB 用内存 sqlite，不触真实库）。

回归背景（spec review on eeff54f）：每文档 question 覆盖率查询 SELECT 了
max(d.filename) 却漏写 JOIN documents——psycopg2 UndefinedTable 在 gate
打印完 overall 覆盖行后直接炸掉，后续所有检查与退出码路径全部中断。

锁定方式：把覆盖率 SQL 抽成模块常量 DOC_COVERAGE_SQL，这里在内存 sqlite
里按真实 schema 建三表后【真实执行】它（而非字符串断言）——任何缺表、
缺 JOIN、错列名的回归都会在 execute 处当场抛错。只用标准 SQL
（LEFT JOIN / COUNT DISTINCT / MAX / GROUP BY），sqlite 与 PG 行为一致。
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))

from index_integrity_report import DOC_COVERAGE_SQL  # noqa: E402


@pytest.fixture()
def sqlite_conn():
    """按 db.py 真实列名建 chunks / chunk_questions / documents 三表的内存库。"""
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, document_id VARCHAR(64) UNIQUE NOT NULL, "
                "filename VARCHAR(256) NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE chunks ("
                "id INTEGER PRIMARY KEY, chunk_id VARCHAR(64) UNIQUE NOT NULL, "
                "document_id VARCHAR(64) NOT NULL REFERENCES documents(document_id))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE chunk_questions ("
                "id INTEGER PRIMARY KEY, chunk_id VARCHAR(64) NOT NULL, "
                "question TEXT NOT NULL)"
            )
        )
        # doc1：3 chunk / 1 有问题（且该 chunk 有 2 条问题 → DISTINCT 计 1）；
        # doc2：2 chunk 全覆盖；doc3：1 chunk 零问题（批降级场景，filename 必须仍能取出）。
        rows = [
            ("d1", "doc1.pdf"),
            ("d2", "doc2.pdf"),
            ("d3", "doc3.pdf"),
        ]
        for did, fname in rows:
            conn.execute(
                text("INSERT INTO documents (document_id, filename) VALUES (:d, :f)"),
                {"d": did, "f": fname},
            )
        chunks = [
            ("c11", "d1"),
            ("c12", "d1"),
            ("c13", "d1"),
            ("c21", "d2"),
            ("c22", "d2"),
            ("c31", "d3"),
        ]
        for cid, did in chunks:
            conn.execute(
                text("INSERT INTO chunks (chunk_id, document_id) VALUES (:c, :d)"),
                {"c": cid, "d": did},
            )
        questions = [
            ("q1", "c11", "问题一？"),
            ("q2", "c11", "问题二？"),
            ("q3", "c21", "问题三？"),
            ("q4", "c22", "问题四？"),
        ]
        for qid, cid, q in questions:
            conn.execute(
                text("INSERT INTO chunk_questions (chunk_id, question) VALUES (:c, :q)"),
                {"c": cid, "q": q},
            )
        yield conn


def test_doc_coverage_sql_executes_with_join(sqlite_conn):
    """SQL 可执行（曾因漏 JOIN documents 而 UndefinedTable），且 filename 正常回显。"""
    rows = sqlite_conn.execute(text(DOC_COVERAGE_SQL)).fetchall()

    assert len(rows) == 3
    by_doc = {r.document_id: r for r in rows}

    # filename 来自 JOIN 上的 documents 表——若 JOIN 缺失/列名错，此处早炸或为 NULL
    assert by_doc["d1"].filename == "doc1.pdf"

    # covered = 有问题的 chunk 数（DISTINCT chunk_id）；total = 该文档全部 chunk 数
    assert (by_doc["d1"].covered, by_doc["d1"].total) == (1, 3)
    assert (by_doc["d2"].covered, by_doc["d2"].total) == (2, 2)
    assert (by_doc["d3"].covered, by_doc["d3"].total) == (0, 1)


def test_doc_coverage_sql_left_join_keeps_unanswered_docs(sqlite_conn):
    """零问题文档不得被内连接语义吞掉——降级批次必须仍出现在 per-doc 明细里。"""
    sqlite_conn.execute(text("DELETE FROM chunk_questions"))

    rows = sqlite_conn.execute(text(DOC_COVERAGE_SQL)).fetchall()

    assert len(rows) == 3, "LEFT JOIN 语义：无问题的文档也必须出现（covered=0）"
    assert all(r.covered == 0 for r in rows)

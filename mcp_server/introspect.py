"""PG 结构自省（只读连接）：类型/FK 来自 information_schema 系视图，
语义来自 COMMENT ON（col_description/obj_description）。

低基数枚举采样：白名单内类型（varchar/bpchar/text/bool/int2/int4）
且 distinct ≤20 时，原样返回真实枚举值。PII 审计由上游
``app/core/pii_scanner.py`` 在摄入侧承担；本函数不脱敏。

同步 API，预期作为离线批处理场景专用；async 调用方需自行
``asyncio.to_thread(...)`` 包裹。
"""
from __future__ import annotations

import psycopg2
from psycopg2 import sql as psql

_ENUM_BASE_TYPES = {"varchar", "bpchar", "text", "bool", "int2", "int4"}
_ENUM_MAX_DISTINCT = 20


def introspect_schema(dsn: str, schema: str = "public", tables: list[str] | None = None) -> list[dict]:
    conn = psycopg2.connect(
        dsn,
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                (schema,),
            )
            names = [r[0] for r in cur.fetchall()]
            if tables:
                wanted = set(tables)
                names = [t for t in names if t in wanted]
            results = []
            for t in names:
                try:
                    results.append(_introspect_table(cur, schema, t))
                except Exception as exc:  # noqa: BLE001 — 单表失败隔离，整批继续
                    results.append({
                        "schema": schema, "table": t, "table_comment": "",
                        "columns": [], "error": f"{type(exc).__name__}: {exc}",
                    })
            return results
    finally:
        conn.close()


def _introspect_table(cur, schema: str, table: str) -> dict:
    cur.execute("SELECT obj_description(%s::regclass, 'pg_class')", (f"{schema}.{table}",))
    row = cur.fetchone()
    table_comment = (row[0] if row else "") or ""

    cur.execute(
        """
        SELECT a.attname,
               format_type(a.atttypid, a.atttypmod),
               col_description(a.attrelid, a.attnum),
               t.typname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE n.nspname = %s AND c.relname = %s
          AND a.attnum > 0 AND NOT a.attisdropped
          AND a.attgenerated = ''
        ORDER BY a.attnum
        """,
        (schema, table),
    )
    columns = []
    for name, col_type, comment, typname in cur.fetchall():
        col = {"name": name, "type": col_type, "comment": comment or "", "enums": None, "fk": None}
        fk = _fk_target(cur, schema, table, name)
        if fk:
            col["fk"] = fk
        elif typname in _ENUM_BASE_TYPES:
            col["enums"] = _sample_distinct(cur, schema, table, name)
        columns.append(col)
    return {"schema": schema, "table": table, "table_comment": table_comment, "columns": columns}


def _fk_target(cur, schema: str, table: str, column: str) -> str | None:
    cur.execute(
        """
        SELECT ccu.table_schema, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = %s AND tc.table_name = %s AND kcu.column_name = %s
        LIMIT 1
        """,
        (schema, table, column),
    )
    row = cur.fetchone()
    return f"{row[0]}.{row[1]}.{row[2]}" if row else None


def _sample_distinct(cur, schema: str, table: str, column: str) -> list | None:
    """distinct ≤20 → 返回枚举值；超限 / 全 NULL → None（视为自由值列，不采样）。"""
    query = psql.SQL(
        "SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL LIMIT %s"
    ).format(col=psql.Identifier(column), tbl=psql.Identifier(schema, table))
    cur.execute(query, (_ENUM_MAX_DISTINCT + 1,))
    rows = cur.fetchall()
    vals = [r[0] for r in rows]
    if not vals:
        return None
    if len(vals) > _ENUM_MAX_DISTINCT:
        return None
    return vals
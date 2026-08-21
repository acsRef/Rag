"""PG 自省：fake cursor 驱动——列注释、FK、低基数枚举采样、表过滤。"""


class FakeCursor:
    """按执行顺序回放预设结果集；记录 SQL 供断言。

    若某一格的 execute 抛错预设（用 ``("raise", exc)`` 标记），则在该次
    execute 时抛出该异常（用于模拟 PG 端失败）。
    """

    def __init__(self, results: list[list[tuple]]):
        self._results = list(results)
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params or ()))
        if self._results and self._results[0] == ["raise"]:
            self._results.pop(0)
            raise RuntimeError("synthetic pg failure")

    def fetchall(self):
        next_ = self._results.pop(0)
        if next_ == [None]:
            return None
        return next_

    def fetchone(self):
        rows = self._results.pop(0)
        return rows[0] if rows else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def test_introspect_builds_columns_with_comment_enum_fk(monkeypatch):
    import mcp_server.introspect as mod

    # sale_id 走 _sample_distinct：distinct 超限（21 行）→ enums=None
    sale_id_over_limit = [(i,) for i in range(1, 22)]
    cur = FakeCursor(
        [
            [("fact_sales",)],  # tables 列表
            [("销售记录事实表",)],  # obj_description
            [  # pg_attribute 列（attnum 升序）
                ("sale_id", "integer", "销售记录主键", "int4"),
                ("channel", "character varying(10)", "销售渠道", "varchar"),
                ("date_id", "integer", "销售日期", "int4"),
            ],
            [],  # sale_id 的 FK 查询：无 → 走枚举采样
            sale_id_over_limit,  # sale_id 枚举采样：distinct 超限哨兵（21 行 > _ENUM_MAX_DISTINCT=20）
            [],  # channel 的 FK 查询：无 → 走枚举采样
            [("线上",), ("线下",)],  # channel 的枚举采样（FK 查询无结果 → 走枚举采样）
            [("public", "dim_date", "date_id")],  # date_id 的 FK 查询：命中
        ]
    )
    monkeypatch.setattr(
        mod.psycopg2, "connect", lambda dsn, connect_timeout=5, options="": FakeConn(cur)
    )

    infos = mod.introspect_schema("postgresql://fake", "public")
    assert len(infos) == 1
    info = infos[0]
    assert info["table"] == "fact_sales"
    assert info["table_comment"] == "销售记录事实表"
    by_name = {c["name"]: c for c in info["columns"]}
    assert by_name["channel"]["enums"] == ["线上", "线下"]
    assert by_name["date_id"]["fk"] == "public.dim_date.date_id"
    assert by_name["sale_id"]["enums"] is None


def test_introspect_all_null_column_returns_no_enums(monkeypatch):
    """全 NULL 列（distinct WHERE IS NOT NULL → []）不能当作低基数枚举，应返回 None。"""
    import mcp_server.introspect as mod

    cur = FakeCursor(
        [
            [("dim_status",)],  # tables 列表
            [("状态维度表",)],  # obj_description
            [  # pg_attribute 列
                ("status_code", "varchar(10)", "状态码", "varchar"),
            ],
            [],  # status_code 的 FK 查询：无 → 走枚举采样
            [],  # status_code 枚举采样：全 NULL → []
        ]
    )
    monkeypatch.setattr(
        mod.psycopg2, "connect", lambda dsn, connect_timeout=5, options="": FakeConn(cur)
    )

    infos = mod.introspect_schema("postgresql://fake", "public")
    by_name = {c["name"]: c for c in infos[0]["columns"]}
    assert by_name["status_code"]["enums"] is None


def test_introspect_table_filter(monkeypatch):
    import mcp_server.introspect as mod

    cur = FakeCursor(
        [
            [("fact_sales",), ("dim_date",)],  # tables 列表（过滤发生在 Python 侧）
            [("销售记录事实表",)],
            [],  # 无列（简化）
        ]
    )
    monkeypatch.setattr(
        mod.psycopg2, "connect", lambda dsn, connect_timeout=5, options="": FakeConn(cur)
    )
    infos = mod.introspect_schema("postgresql://fake", "public", tables=["fact_sales"])
    assert [i["table"] for i in infos] == ["fact_sales"]


def test_introspect_isolates_failing_table(monkeypatch):
    """单表自省失败时，其他表仍可返回，并附带 error 字段而不抛异常。"""
    import mcp_server.introspect as mod

    cur = FakeCursor(
        [
            [("fact_sales",), ("dim_date",)],  # tables 列表（两张）
            [("销售记录事实表",)],  # fact_sales obj_description
            ["raise"],  # fact_sales pg_attribute → 抛错
            [("日期维度表",)],  # dim_date obj_description
            [],  # dim_date 无列 → 正常返回
        ]
    )
    monkeypatch.setattr(
        mod.psycopg2, "connect", lambda dsn, connect_timeout=5, options="": FakeConn(cur)
    )

    infos = mod.introspect_schema("postgresql://fake", "public")
    by_table = {i["table"]: i for i in infos}
    assert "error" in by_table["fact_sales"] and "synthetic" in by_table["fact_sales"]["error"]
    assert by_table["fact_sales"]["columns"] == []
    assert "error" not in by_table["dim_date"]
    assert by_table["dim_date"]["table_comment"] == "日期维度表"

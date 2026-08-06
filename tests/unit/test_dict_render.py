"""数据字典 Markdown 渲染：文件名幂等键 + 表文档 + 接口文档（含长连接分节）。"""
from mcp_server.render import (
    api_filename,
    render_api_doc,
    render_table_doc,
    table_filename,
)


def test_filenames_are_idempotency_keys():
    assert table_filename("public", "fact_sales") == "dict-table_public_fact_sales.md"
    assert api_filename("orders-push") == "dict-api_orders-push.md"


def test_render_table_doc_contains_comment_and_enums():
    md = render_table_doc(
        schema="public", table="fact_sales",
        table_comment="销售记录事实表",
        columns=[
            {"name": "sale_id", "type": "integer", "comment": "销售记录主键", "enums": None, "fk": None},
            {"name": "channel", "type": "character varying(10)", "comment": "销售渠道",
             "enums": ["线上", "线下"], "fk": None},
            {"name": "date_id", "type": "integer", "comment": "销售日期", "enums": None,
             "fk": "public.dim_date.date_id"},
        ],
    )
    assert "# 表 `public.fact_sales`" in md
    assert "销售记录事实表" in md
    assert "| channel | character varying(10) | 销售渠道 |" in md
    assert "线上 / 线下" in md
    assert "FK → public.dim_date.date_id" in md


def test_render_api_doc_http():
    md = render_api_doc(
        name="orders", description="订单查询接口", protocol="http",
        endpoint="GET /v1/orders", auth="Bearer",
        fields=[
            {"name": "order_id", "type": "string", "required": True, "desc": "订单号", "example": "SO-1"},
            {"name": "amt", "type": "number", "required": False, "desc": "订单金额", "example": "99.5"},
        ],
    )
    assert "# 接口字典: orders" in md
    assert "接口类型: HTTP 请求/响应" in md
    assert "| amt | number | 否 | 订单金额 | 99.5 |" in md


def test_render_api_doc_websocket_message_grouping():
    md = render_api_doc(
        name="market-push", description="行情长连接", protocol="websocket",
        endpoint="wss://example.com/push", auth="",
        fields=[
            {"name": "price", "type": "number", "required": True, "desc": "最新价",
             "example": "12.3", "message": "on_message"},
            {"name": "hb", "type": "string", "required": False, "desc": "心跳标识",
             "example": "ping", "message": "heartbeat"},
        ],
    )
    assert "接口类型: WebSocket 长连接" in md
    assert "消息 `on_message` 字段" in md
    assert "消息 `heartbeat` 字段" in md

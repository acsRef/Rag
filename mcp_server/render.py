"""数据字典 Markdown 渲染——唯一写入格式的地方。

文件名即幂等键：ragent-py 上传按「同 kb 同名」复用 document_id，
内容 hash 未变的 chunk 复用既有 embedding（增量摄入管线既有能力）。
"""
from __future__ import annotations

_PROTOCOL_LABELS = {
    "http": "HTTP 请求/响应",
    "websocket": "WebSocket 长连接",
    "sse": "SSE 服务端推送",
    "long_poll": "长轮询",
}


def table_filename(schema: str, table: str) -> str:
    return f"dict-table_{schema}_{table}.md"


def api_filename(name: str) -> str:
    return f"dict-api_{name}.md"


def render_table_doc(*, schema: str, table: str, table_comment: str, columns: list[dict]) -> str:
    """columns 每项: {name, type, comment, enums: list|None, fk: str|None}"""
    lines = [f"# 表 `{schema}.{table}`", ""]
    if table_comment:
        lines += [table_comment, ""]
    lines += ["## 字段", "", "| 字段 | 类型 | 含义 | 枚举/FK |", "|---|---|---|---|"]
    for c in columns:
        extra = []
        if c.get("fk"):
            extra.append(f"FK → {c['fk']}")
        if c.get("enums"):
            extra.append("枚举值: " + " / ".join(str(v) for v in c["enums"]))
        lines.append(f"| {c['name']} | {c['type']} | {c.get('comment') or ''} | {'; '.join(extra)} |")
    return "\n".join(lines) + "\n"


def render_api_doc(*, name: str, description: str = "", protocol: str = "http",
                   endpoint: str = "", auth: str = "", fields: list[dict]) -> str:
    """fields 每项: {name, type, required, desc, example, message?: str}

    protocol ∈ http/websocket/sse/long_poll；流式接口的字段用 message
    归属到具体消息/事件类型分节。帧时序/心跳/重连语义不进 v1。
    """
    lines = [f"# 接口字典: {name}", ""]
    lines.append(f"- 接口类型: {_PROTOCOL_LABELS.get(protocol, protocol)}")
    if endpoint:
        lines.append(f"- 地址: `{endpoint}`")
    if auth:
        lines.append(f"- 认证: {auth}")
    if description:
        lines += ["", description]

    by_message: dict[str, list[dict]] = {}
    for f in fields:
        by_message.setdefault(f.get("message") or "", []).append(f)
    for msg, fs in by_message.items():
        title = "字段" if not msg else f"消息 `{msg}` 字段"
        lines += ["", f"## {title}", "",
                  "| 字段 | 类型 | 必填 | 含义 | 示例 |", "|---|---|---|---|---|"]
        for f in fs:
            req = "是" if f.get("required") else "否"
            lines.append(
                f"| {f['name']} | {f.get('type', '')} | {req} | {f.get('desc', '')} | {f.get('example', '')} |"
            )
    return "\n".join(lines) + "\n"

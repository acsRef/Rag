"""跨进程共享的 ragent-py 登录 token 缓存（文件持久化）。

根治登录 429：多个进程（RagentClient / interface_dict_tools / mcp_schema_server）
各自登录会撞 ragent-py 每 IP 5 分钟 10 次限流。共享一个 token 缓存文件后，
全系统约 1 次登录 / token 生命周期（24h，缓存 TTL 默认 20h 留余量），
401 时由调用方 invalidate 后重登。

文件格式：{ "<RAGENT_URL>": { "token": "...", "expires_at": <unix> } }
"""
from __future__ import annotations

import json
import os
import threading
import time

_PATH = os.getenv("RAGENT_TOKEN_CACHE", os.path.join(os.path.expanduser("~"), ".ragent_token_cache.json"))
# token 实际 24h，缓存留 4h 安全余量，避免服务端先于本地过期导致 401 风暴。
_TTL = float(os.getenv("RAGENT_TOKEN_TTL", str(20 * 3600)))

_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass  # 写失败不影响主流程，下次登录重试


def get_token(base_url: str) -> str | None:
    """返回未过期的缓存 token；无/过期返回 None。"""
    with _lock:
        entry = _load().get(base_url)
        if entry and entry.get("token") and entry.get("expires_at", 0) > time.time():
            return entry["token"]
        return None


def set_token(base_url: str, token: str) -> None:
    """登录成功后写入缓存。"""
    with _lock:
        data = _load()
        data[base_url] = {"token": token, "expires_at": time.time() + _TTL}
        _save(data)


def invalidate(base_url: str) -> None:
    """401 时清除缓存，触发下一次真实登录。"""
    with _lock:
        data = _load()
        if base_url in data:
            data.pop(base_url, None)
            _save(data)
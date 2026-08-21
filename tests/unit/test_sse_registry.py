"""sse-disconnect-continue：注册表清理纯逻辑（离线）。

客户端断开后生产者后台跑完；注册表移除时须确认当前任务仍是自己，
避免旧任务收尾误删新请求的任务。
"""

import asyncio

from app.api.chat import _IN_FLIGHT, _release_in_flight


def _reset():
    _IN_FLIGHT.clear()


async def _wait_task(t):
    try:
        await t
    except asyncio.CancelledError:
        pass


async def test_release_removes_same_task():
    _reset()
    t = asyncio.create_task(asyncio.sleep(0))
    _IN_FLIGHT["conv-x"] = t
    _release_in_flight("conv-x", t)
    assert "conv-x" not in _IN_FLIGHT
    t.cancel()
    await _wait_task(t)


async def test_release_keeps_newer_task():
    _reset()
    old = asyncio.create_task(asyncio.sleep(0))
    new = asyncio.create_task(asyncio.sleep(0))
    _IN_FLIGHT["conv-x"] = new
    _release_in_flight("conv-x", old)  # 旧任务收尾误调清理
    assert _IN_FLIGHT.get("conv-x") is new
    old.cancel()
    new.cancel()
    await _wait_task(old)
    await _wait_task(new)

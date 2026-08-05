"""并发 add_message 触发的摘要任务不得基于同一旧快照重复摘要同区间。

旧实现锁在读取之后才获取：连续两次 add_message 都触发 fire-and-forget
任务 → 两个任务基于同一份旧 summary/watermark 各自建 prompt（区间重叠）→
再串行拿锁写入 → 摘要区间被同位置折叠两次（inflated summary）。

修复后锁在读取之前：两个并发任务只能有一个进入读-改-写，另一个拿锁失败
直接跳过；并发的摘要区间集合（fake LLM 记录的 new_turns 文本去解析得到的
消息集合）必须互斥。
"""
import asyncio

import pytest

_USERS = ("race-user",)


@pytest.fixture(autouse=True)
def _concurrency_users(integration_db):
    from app.store.db import get_db_ctx, User

    with get_db_ctx() as session:
        for uid in _USERS:
            if not session.query(User).filter_by(id=uid).first():
                session.add(User(id=uid, username=uid, hashed_password="unused", is_active=True))
        session.commit()
    yield


async def test_concurrent_summaries_cover_disjoint_intervals(
        integration_db, monkeypatch, fake_llm_stack):
    """两个并发 fire-and-forget 摘要任务各自记录其 prompt 涵盖的消息集合；
    两个集合必须互斥（不重复摘要同一区间）。"""
    from app.core import memory as memory_mod
    from app.core.memory import conversation_memory
    from app.llm.chat import minimax_client
    from app.store.db import get_db_ctx, Conversation, Message

    # 重新设置参数让触发条件容易满足
    monkeypatch.setattr(memory_mod.settings, "history_max_tokens", 60)
    monkeypatch.setattr(memory_mod.settings, "summary_trigger_tokens", 30)

    # 每条 message 40 token（"L%x%s" with 60 chars * 3），窗口 1.5 条 ≈ 1.5 条
    captured: list[str] = []

    async def fake_chat(messages, **kw):
        prompt = messages[-1]["content"]
        captured.append(prompt)
        return "## 主题与结论\n摘要文本"

    monkeypatch.setattr(minimax_client, "chat", fake_chat)

    # 创建 conversation
    conv = await asyncio.to_thread(
        conversation_memory.get_or_create_conversation, None, "race-user")
    # 用足够多的消息让两个并发摘要同时触发（每条约 24 token → 窗口装 2 条 →
    # 第 3 条起触发摘要；连续 add_message 让两个任务并发触发）
    for i in range(10):
        await conversation_memory.add_message(
            conv, "user", "M%d" % i + "x" * 33,
            user_id="race-user")
    # 让 fire-and-forget 摘要任务 + 排水循环跑完；0.5s 足够并发场景触发
    # ≥2 次 LLM 调用（每条 add_message 后任务启动；锁竞争让其中一些返回 False）
    await asyncio.sleep(0.5)

    # 解析每次 LLM 调用 prompt 里的「新增对话」段落里的消息索引
    def parse_msg_indices(prompt: str) -> set[int]:
        # prompt 格式："新增对话: user: M5xx...user: M6xx..."
        idxs = set()
        for line in prompt.splitlines():
            if line.startswith("user: M") or line.startswith("assistant: M"):
                idx_s = line.split(":", 2)[1].strip()
                # 去掉前缀 "M" 与尾部 "xx..."，取数字
                idx_str = idx_s.lstrip("M").rstrip("x").rstrip("X")
                if idx_str.isdigit():
                    idxs.add(int(idx_str))
        return idxs

    intervals = [parse_msg_indices(p) for p in captured]
    assert len(intervals) >= 2, (
        "并发场景必须实际触发 ≥2 次 LLM 调用；本次仅 %d 次" % len(intervals))
    # 不变量：任意两次摘要的区间互不重叠（修复前 = 同一区间被折叠两次）
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            overlap = intervals[i] & intervals[j]
            assert not overlap, (
                "摘要区间重叠：第 %d 次调用 [len=%d] 与第 %d 次 [len=%d] 重叠元素 %s"
                % (i, len(intervals[i]), j, len(intervals[j]), sorted(overlap)))

    # 锁前移后：每次摘要必然自上次 watermark 之后推进；区间连续覆盖
    # 已扫描的消息（最多偶发的轻微延迟，但不重叠）
    all_idxs: set[int] = set()
    for iv in intervals:
        all_idxs |= iv
    assert all_idxs, "应有消息被纳入摘要"
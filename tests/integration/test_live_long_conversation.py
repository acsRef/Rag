"""长对话记忆端到端验证：真实 LLM 跑 16 轮对话，断言记忆机制不变式。

覆盖：窗口滑动、摘要异步触发与水位推进、无丢失不变式、标题生成、
预算约束。仅当 RAGENT_LIVE_LLM=1 且 key 齐备时运行（约分钟级）。
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.config import settings

pytestmark = pytest.mark.live_llm

TABLE_DIR = Path(__file__).parent.parent / "fixtures" / "docs-tables"

# 16 轮：事实问答 → 代词追问（触发 rewrite）→ 汇总/对比（触发拆分路径）
TURNS = [
    "华东区2024年全年销售额是多少？同比增长多少？",
    "华东区第一大产品线是什么？全年销售额多少？",
    "智享家Pro在华东区Q3的销售额是多少？",
    "它的Q3同比增速怎么样？",
    "这个产品线在华南区卖了多少？和华东差多少？",
    "华东区哪个渠道贡献最大？达成率多少？",
    "这个渠道归属哪个团队？",
    "华东区的毛利率各季度走势如何？",
    "Q4毛利率是多少？",
    "华东区增速最快的产品线是哪个？",
    "安芯保在华东区全年表现怎么样？",
    "乐活贷在华东区和华南区分别是什么情况？",
    "帮我总结一下华东区2024年的经营要点",
    "前面提到的Q1销售额具体数字再给我一遍",
    "对比一下华东和华南两区的整体差异",
    "用一句话概括我们这次讨论的核心结论",
]


@pytest.fixture(scope="module")
def corpus(integration_db, live_env):
    """摄入华东/华南两份销售文档作为对话语料。"""
    from app.ingestion.indexer import document_indexer
    from tests.integration.conftest import truncate_corpus

    truncate_corpus(integration_db)
    ids = {}
    for name in ("sales_east_2024.md", "sales_south_2024.md"):
        res = document_indexer.index(
            name, (TABLE_DIR / name).read_bytes(),
            kb_id="test-kb", user_id="test-user",
        )
        assert res["status"] == "indexed", "摄入 %s 失败: %s" % (name, res)
        ids[name] = res["document_id"]
    return ids


async def _run_turn(conv_id, query):
    """跑一轮 pipeline，收集 SSE 事件，返回 (answer_text, event_kinds)。"""
    from app.core.pipeline import rag_pipeline
    from app.models.schemas import ChatRequest

    answer_parts = []
    kinds = set()
    last_event = ""
    async for raw in rag_pipeline.execute(
        ChatRequest(conversation_id=conv_id, query=query),
        user_id="long-conv-user",
        can_read_all=True,
    ):
        # pipeline 单次 yield 可能是多行 SSE 块，必须按行拆分
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("event: "):
                last_event = line[7:].strip()
                kinds.add(last_event)
            elif line.startswith("data: "):
                data = line[6:]
                if last_event == "token":
                    answer_parts.append(data)
                elif last_event == "metadata" and not conv_id:
                    conv_id = json.loads(data).get("conversation_id", conv_id)
    return "".join(answer_parts), kinds, conv_id


async def test_long_conversation_memory_invariants(corpus):
    """16 轮真实对话后，记忆机制的全部不变式必须成立。"""
    from app.core.memory import _HISTORY_SCAN_LIMIT, _estimate_tokens, conversation_memory

    # 预建用户（conversations.user_id 有外键）
    from app.store.db import Conversation, Message, User, get_db_ctx
    with get_db_ctx() as session:
        if not session.query(User).filter(User.id == "long-conv-user").first():
            session.add(User(id="long-conv-user", username="long-conv-user",
                             hashed_password="unused", is_active=True))
            session.commit()

    conv_id = None
    answered = 0
    for i, q in enumerate(TURNS):
        answer, kinds, conv_id = await _run_turn(conv_id, q)
        assert conv_id, "第 %d 轮未返回 conversation_id" % (i + 1)
        # 每轮要么有答案，要么有显式的错误/空检索信号——不允许静默无响应
        assert ("token" in kinds) or ("error" in kinds) or ("no_context" in kinds), \
            "第 %d 轮既无 token 也无 error/no_context 事件" % (i + 1)
        if "token" in kinds and answer.strip():
            answered += 1
        await asyncio.sleep(0.5)   # 给后台摘要任务留运行窗口

    assert answered >= 12, "16 轮中仅 %d 轮产出答案，过多失败" % answered

    # 等待摘要收敛：排水式摘要单次调用即收敛到位，但后台任务持锁时
    # 本次调用会立即返回——轮询直到水位覆盖全部窗口外消息（最多 120s）
    import time as _time

    from app.core.memory import conversation_memory as _cm
    deadline = _time.monotonic() + 180
    while _time.monotonic() < deadline:
        # 清退避态：对话期间的偶发 LLM 失败会计入退避（该行为由
        # test_summary_failure_backoff 专测）；此处只验证收敛性本身
        from app.core.memory import _summary_failures
        _summary_failures.pop(conv_id, None)
        await _cm._maybe_summarize(conv_id)
        with get_db_ctx() as session:
            conv_chk = session.query(Conversation).filter_by(
                conversation_id=conv_id).first()
            wm = conv_chk.last_summarized_msg_id or 0
            rows_chk = [(m.id, m.content or "") for m in session.query(Message)
                        .filter_by(conversation_id=conv_id)
                        .order_by(Message.id.asc()).all()]
        rec = rows_chk[-_HISTORY_SCAN_LIMIT:]
        acc2, bnd = 0, rec[-1][0]
        for mid, content in reversed(rec):
            t = _estimate_tokens(content)
            if acc2 + t > settings.history_max_tokens:
                break
            acc2 += t
            bnd = mid
        gap = sum(_estimate_tokens(c) for mid, c in rows_chk
                  if mid < bnd and mid > wm)
        if gap <= settings.summary_trigger_tokens:
            break
        await asyncio.sleep(2)

    with get_db_ctx() as session:
        conv = session.query(Conversation).filter_by(conversation_id=conv_id).first()
        assert conv is not None
        msgs = (session.query(Message)
                .filter_by(conversation_id=conv_id)
                .order_by(Message.id.asc()).all())
        msg_rows = [(m.id, m.role, m.content or "", m.status) for m in msgs]
        summary = conv.summary or ""
        watermark = conv.last_summarized_msg_id

    # 1) 消息完整性：16 轮 user + 至少 12 条 assistant，无 streaming 残留
    roles = [r for _, r, _, _ in msg_rows]
    assert roles.count("user") == len(TURNS)
    assert roles.count("assistant") >= 12
    assert all(st != "streaming" for _, _, _, st in msg_rows), "存在 streaming 残留消息"

    # 2) 标题：首轮 query 前缀
    assert conv.title and conv.title in TURNS[0], "标题应为首轮 query 前缀，实际 %r" % conv.title

    # 3) 摘要与水位：16 轮（约 3000+ token 用户消息）必然触发过摘要
    assert summary.strip(), "16 轮对话后 summary 仍为空"
    assert watermark, "last_summarized_msg_id 为空（水位未推进）"

    # 4) 有界滞后不变式：摘要按"溢出累积到 summary_trigger_tokens 才触发"
    #    批量执行，因此任意时刻"既不在窗口、又未被摘要"的消息总 token 量
    #    不得超过触发阈值（超了说明摘要机制失效，会越拖越多）
    from app.config import settings as st
    recent = [(mid, content) for mid, _, content, _ in msg_rows][- _HISTORY_SCAN_LIMIT:]
    acc, boundary = 0, recent[-1][0]
    for mid, content in reversed(recent):
        t = _estimate_tokens(content)
        if acc + t > st.history_max_tokens:
            break
        acc += t
        boundary = mid
    gap = sum(_estimate_tokens(content)
              for mid, _, content, _ in msg_rows
              if mid < boundary and mid > (watermark or 0))
    assert gap <= st.summary_trigger_tokens, (
        "摘要滞后越界：未覆盖溢出 %d token > 触发阈值 %d（水位 %s，边界 %s）"
        % (gap, st.summary_trigger_tokens, watermark, boundary))

    # 5) 预算约束：get_history 返回内容不超 token 预算
    history = conversation_memory.get_history(conv_id)
    total = sum(_estimate_tokens(m["content"]) for m in history)
    assert total <= st.history_max_tokens, "历史窗口超预算：%d > %d" % (
        total, st.history_max_tokens)

    # 6) 摘要质量抽查：应含早期轮次的实体（证明增量摘要未丢早期信息）
    assert ("华东" in summary) or ("销售" in summary), "摘要未覆盖对话核心实体"

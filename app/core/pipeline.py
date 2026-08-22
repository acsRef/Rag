import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator

from app.config import settings
from app.core.diagnostics import DiagContext
from app.core.doc_relation import cross_doc_synthesizer
from app.core.evidence import build_evidence_result, evidence_gate_should_refuse, evidence_organizer
from app.core.intent import intent_classifier
from app.core.memory import conversation_memory
from app.core.prompt import prompt_builder
from app.core.query_parser import parse_query
from app.core.retrieval import retrieval_engine
from app.core.rewrite import query_rewrite_service
from app.core.tag_parser import TagStreamParser
from app.llm.base import CircuitOpenError, provider_health
from app.llm.chat import minimax_client
from app.models.schemas import ChatRequest, RetrievedChunk, SourceInfo
from app.store import pgvector_store
from app.store.db import Document

logger = logging.getLogger(__name__)
import time


def _pii_safe(text: str) -> str:
    """Mask PII in text if PII filtering is enabled."""
    if not settings.pii_enabled:
        return text
    from app.core.pii_scanner import mask_text

    return mask_text(text)


def _resolve_doc_map(chunks: list[RetrievedChunk]) -> dict[str, str]:
    """document_id → filename 映射，供 sources 组装与跨文档标注共用。"""
    doc_map: dict[str, str] = {}
    doc_ids = list({c.document_id for c in chunks if c.document_id})
    if doc_ids:
        from app.store.db import Document, get_db_ctx

        with get_db_ctx() as session:
            rows = (
                session.query(Document.document_id, Document.filename)
                .filter(Document.document_id.in_(doc_ids))
                .all()
            )
            for row in rows:
                doc_map[row.document_id] = row.filename
    return doc_map


def _build_sources(chunks: list[RetrievedChunk], doc_map: dict[str, str]) -> list[SourceInfo]:
    """Build SourceInfo list for frontend. 必须在跨文档合并去重之后调用，
    保证 [Source N] 编号与 UI 来源卡片、prompt 内编号三者一致。"""
    if not chunks:
        return []
    sources = []
    for c in chunks:
        text = c.text[:150].replace("\n", " ")
        sources.append(
            SourceInfo(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                filename=doc_map.get(c.document_id, ""),
                title=c.title,
                section_path=c.section_path,
                snippet=text,
                score=round(c.score, 4),
            )
        )
    return sources


_NL = "\\n"  # literal backslash-n for SSE JSON encoding


def _sse_safe(text: str) -> str:
    """Escape text for safe SSE data field (remove \r, encode \n)."""
    return text.replace(chr(10), _NL).replace(chr(13), "")


_EOL = chr(10)
_EOL2 = chr(10) * 2


def _norm(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse blank lines between consecutive list items
    text = re.sub(r"(\n\s*(?:[-*]|\d+\.)\s.+\n)\n+(?=\s*(?:[-*]|\d+\.)\s)", r"\1", text)
    return text.strip()


def _truncate_with_doc_diversity(chunks: list, max_total: int) -> list:
    """截断 chunk 列表但保证文档多样性。

    策略：每个 document_id 至少保留 1 个 chunk（最高分），
    剩余名额按分数填充，总数不超过 max_total。

    旧实现直接 `chunks[:rerank_top_k]` 按分数截断，
    跨文档/跨年份对比时，低分年份的 chunk 可能被高分年份全部挤掉。
    """
    if not chunks or max_total <= 0:
        return chunks[:max_total] if max_total > 0 else []

    doc_ids_seen: set[str] = set()
    diverse: list = []
    remaining: list = []

    for c in chunks:
        doc_id = getattr(c, "document_id", "") or ""
        if doc_id and doc_id not in doc_ids_seen:
            doc_ids_seen.add(doc_id)
            diverse.append(c)
        else:
            remaining.append(c)

    # 每个文档至少 1 条后，按分数填充剩余名额
    for c in remaining:
        if len(diverse) >= max_total:
            break
        diverse.append(c)

    return diverse


def resolve_doc_ids_by_years(
    years: list[int] | None,
    kb_ids: list[str],
    session,
) -> list[str]:
    """根据年份查 KB 范围内匹配的 document_id（year→doc_id 桥接）。

    用途：query_parser 提取 years 后，pipeline 在 retrieve 之前查 Document.filename
    包含年份的 doc，把 doc_id 塞进 RetrievalFilter.document_ids——使 hybrid_search
    直接 SQL 过滤掉其他年份的 chunks（不等 chunks.year 列，corpus-specific 但立即可用）。

    同步调用；pipeline 必须 to_thread。

    Args:
        years: query_parser.years；空/None → 返回 []
        kb_ids: 候选 KB id 列表；空列表 → 返回 []
        session: SQLAlchemy Session（同步）
    """
    if not years or not kb_ids:
        return []
    try:
        # 简单策略：filename LIKE '%2024%' OR '%2025%' ...；不依赖 _extract_year_from_filename
        from sqlalchemy import or_

        conditions = [Document.filename.like(f"%{y}%") for y in years]
        rows = (
            session.query(Document.document_id)
            .filter(Document.kb_id.in_(kb_ids))
            .filter(or_(*conditions))
            .all()
        )
        return [r[0] for r in rows]
    except Exception:
        logging.getLogger(__name__).exception("resolve_doc_ids_by_years.failed years=%s", years)
        return []


def _needs_decomposition(query: str) -> bool:
    """Return True if query needs sub-question decomposition and KB routing.

    Rules — any match → needs decomposition:
      1. Comparison / contrast patterns
      2. Multiple explicitly named entities (quoted terms)
      3. Reasoning / aggregation / multi-hop markers
      4. Anaphoric pronouns that need resolution
      5. Year range / temporal patterns (2023-2025年, 近三年, etc.)
      6. Potential false premise patterns (为什么X增长, X增加了多少)
    """
    # Rule 1: comparison / contrast
    if re.search(r"(对比|比较|区别|差异|不同|哪个好|哪个更|vs\.?|versus)", query, re.IGNORECASE):
        return True
    if re.search(r"(和|与|跟|同).{1,20}(区别|不同|差异|对比)", query):
        return True

    # Rule 2: multiple explicit entities (quoted / 《》书名号)
    entities = re.findall(r"《[^》]+》|\"[^\"]+\"|'[^']+'", query)
    if len(entities) >= 2:
        return True

    # Rule 3: reasoning / aggregation / multi-hop markers
    if re.search(r"(最多|最少|最高|最低|哪个|哪家|谁是|谁在)", query):
        return True
    if re.search(r"(为什么|为何|原因|因素|影响|如何导致)", query):
        return True
    if re.search(r"(总结|汇总|概括|整体|全年|整个|所有)", query):
        return True

    # Rule 4: anaphoric pronouns (need resolution from context)
    # 不用裸单字 其/该/他/她——会误报 其他/其实/尤其/该文件 等常见词，
    # 给无代词查询强加一次 LLM 改写。
    # 设计审查 P2-15：`(?<!其)它` 排除 `其它`（其+它 的它非代词）；`他们` 等
    # 多字代词不受影响，`其他` 的 他 无裸 他 规则故天然不命中。
    if re.search(r"((?<!其)它|他们|她们|它们|这个|那个|这些|那些|这位|那位|上述|前面|上文)", query):
        return True

    # Rule 5: year range / temporal patterns
    # "2023-2025年", "2023至2025", "近三年", "连续三年", "这几年"
    if re.search(r"\d{4}[-–—至到]\d{4}", query):
        return True
    if re.search(r"(近\d+年|连续\d+年|这几年|历年|各年|每一年|分别)", query):
        return True

    # Rule 6: potential false premise patterns
    # "为什么X增长了", "X增加了多少", "X扩大了多少" — might be wrong premise
    return bool(re.search(r"为什么.{2,20}(增长|增加|扩大|提升|上升|加大|提高)", query))


class RAGPipeline:
    async def execute(
        self,
        req: ChatRequest,
        user_id: str = "anonymous",
        user_role_ids: list[int] | None = None,
        can_read_all: bool = False,
        ctx: DiagContext | None = None,
    ) -> AsyncGenerator[str, None]:
        conv_id = await asyncio.to_thread(
            conversation_memory.get_or_create_conversation,
            req.conversation_id,
            user_id,
        )
        yield f"event: metadata\ndata: {json.dumps({'conversation_id': conv_id})}\n\n"

        if ctx is None and settings.diagnostics_enabled:
            ctx = DiagContext(query=req.query)
            ctx.conversation_id = conv_id

        # 提前解析用户 query 的年份/指标 → diag 日志 + 后续 RetrievalFilter 字段。
        # 当前**不**做 year→doc_id 桥接（中文歧义：2023年X 可能指 X 的 2023 披露 vs 2023 事件
        # 的支付/调整在 2024 年报里；激进过滤会误杀 E 类题）；依赖 chunks.year 列
        # 由 hybrid_search 直接 SQL 过滤。
        parsed_query = parse_query(req.query)
        if ctx:
            ctx.append(
                "query_parse",
                {
                    "raw": parsed_query.raw,
                    "years": parsed_query.years,
                    "intent_metric": parsed_query.intent_metric,
                },
            )
        logging.getLogger(__name__).info(
            "query.parse raw=%r years=%s metric=%s",
            parsed_query.raw[:60],
            parsed_query.years,
            parsed_query.intent_metric,
        )

        if settings.pii_enabled:
            from app.core.pii_scanner import scan_and_reject

            rejects = scan_and_reject(req.query)
            if rejects:
                from app.store.db import PiiAlert, get_session

                session = None
                try:
                    session = get_session()
                    for r in rejects:
                        session.add(
                            PiiAlert(
                                source_type="chat",
                                source_id=conv_id,
                                rule_name=r.rule_name,
                                matched_text=r.matched_text,
                                context_snippet=req.query[max(0, r.start - 30) : r.end + 30],
                                strategy=r.strategy,
                                status="pending",
                            )
                        )
                    session.commit()
                finally:
                    if session:
                        session.close()
                if ctx:
                    ctx.record("rejected", reason="pii", details=[r.rule_name for r in rejects])
                    ctx.save()
                yield 'event: error\ndata: {"error":"您的问题涉及敏感信息，无法回答，请修改后重试"}\n\n'
                yield "event: done\ndata: {}\n\n"
                return

        try:
            history = await asyncio.to_thread(conversation_memory.get_history, conv_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "DB 历史拉取失败（DB-2 穿透兜底）：按空上下文继续"
            )
            history = []
        try:
            summary = await asyncio.to_thread(conversation_memory.get_summary, conv_id)
        except Exception:
            logging.getLogger(__name__).exception("DB 摘要拉取失败（DB-2 穿透兜底）：按空摘要继续")
            summary = ""
        all_kb_ids = req.knowledge_base_ids
        if not all_kb_ids:
            # 默认全库路由：旧实现传 None 会让意图分类器短路，路由从未真正工作
            try:
                all_kb_ids = await asyncio.to_thread(pgvector_store.list_kb_ids)
            except Exception:
                logging.getLogger(__name__).exception(
                    "DB 知识库列表拉取失败（DB-2 穿透兜底）：意图分类短路到无 KB"
                )
                all_kb_ids = []

        # Strategy guard：关掉时强制 needs_decomp=False，跳过 rewrite/intent 走 fast path
        needs_decomp = settings.query_decomposition_enabled and _needs_decomposition(req.query)
        if not needs_decomp:
            # Fast path: no LLM rewrite/intent, search all KBs directly
            sub_queries = [req.query]
            rewritten_query = req.query
        else:
            rewrite_result = await query_rewrite_service.rewrite(
                req.query, history, summary, ctx=ctx
            )
            if ctx:
                ctx.record(
                    "rewrite",
                    original=req.query,
                    rewritten=rewrite_result.rewritten_query,
                    sub_questions=rewrite_result.sub_questions,
                    sub_dependencies=rewrite_result.sub_dependencies,
                )
            sub_queries = rewrite_result.sub_questions
            rewritten_query = rewrite_result.rewritten_query or req.query

        # --- Retrieve ---
        yield 'event: status\ndata: {"phase":"retrieving","message":"正在检索知识库..."}\n\n'
        all_chunks: list[RetrievedChunk] = []
        # 保留子问题→chunks 映射（供证据整理层使用）
        sub_question_chunks: dict[str, list[RetrievedChunk]] = {}

        async def _retrieve_one(sub_q: str) -> tuple[str, list[RetrievedChunk]]:
            intent = None
            if needs_decomp:
                try:
                    intent = await intent_classifier.classify(sub_q, all_kb_ids, ctx=ctx)
                except Exception:
                    # 守卫兜底：意图分类的任何意外不得打死整条检索链路
                    logging.getLogger(__name__).exception("intent.classify_failed q=%s", sub_q[:40])
                    intent = None
                if ctx and intent is not None:
                    ctx.append(
                        "intent",
                        {
                            "sub_query": sub_q,
                            "kbs": [
                                {"name": m.kb_id, "kb_id": m.kb_id, "confidence": m.score}
                                for m in (intent.matches or [])
                            ],
                            "intent_type": intent.intent_type,
                        },
                    )
            try:
                chunks = await retrieval_engine.retrieve(
                    sub_q,
                    intent,
                    user_role_ids=user_role_ids,
                    can_read_all=can_read_all,
                    ctx=ctx,
                    user_id=user_id,
                    filters=parsed_query.filters,
                )
                return sub_q, chunks
            except Exception:
                logging.getLogger(__name__).exception("retrieve.sub_query_failed q=%s", sub_q[:40])
                return sub_q, []

        # 单条状态提示：gather 真正并行前发一次即可，
        # 旧实现按子问题循环发「正在检索子问题 (i/N)」纯误导——
        # 全部 status 事件在 gather 之前就已发出，与实际并发时序对不上。
        if len(sub_queries) > 1:
            yield f"event: status\ndata: {json.dumps({'phase': 'retrieving', 'message': f'正在并行检索 {len(sub_queries)} 个子问题...'})}\n\n"

        if len(sub_queries) > 1:
            results_list = await asyncio.gather(*[_retrieve_one(q) for q in sub_queries])
        else:
            results_list = [await _retrieve_one(sub_queries[0])]
        for sub_q, chunks in results_list:
            sub_question_chunks[sub_q] = chunks
            all_chunks.extend(chunks)

        if not all_chunks:
            try:
                # 兜底用改写后的独立查询——旧实现用原始 query，代词未消解
                chunks = await retrieval_engine.retrieve(
                    rewritten_query,
                    None,
                    user_role_ids=user_role_ids,
                    can_read_all=can_read_all,
                    ctx=ctx,
                    user_id=user_id,
                    filters=parsed_query.filters,
                )
            except Exception:
                chunks = []
            all_chunks.extend(chunks)
            sub_question_chunks[rewritten_query] = chunks

        # Dedup + sort
        seen = set()
        unique_chunks = []
        for c in all_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                unique_chunks.append(c)
        unique_chunks.sort(key=lambda x: x.score, reverse=True)

        # 文档多样性保证：确保每个文档至少有 1 个 chunk 进入最终 prompt
        # 跨文档/跨年份对比时，避免某个文档的 chunks 被全部挤掉
        # 多子查询场景：放宽截断，保留更多 chunks 让多文档数据都能进 prompt
        top_k_limit = settings.rerank_top_k
        if len(sub_queries) > 1:
            top_k_limit = settings.complex_rerank_top_k  # 多文档场景放大到 10

        unique_chunks = _truncate_with_doc_diversity(unique_chunks, top_k_limit)

        # ── Evidence Layer ──
        # 在 cross-doc synthesis 之前 gate：避免对注定拒答的 query 浪费 cross_doc 工作。
        # evidence_gate_enabled 默认 False（保留向后兼容）；启用时：
        #   - coverage < evidence_min_coverage → 拒答/追问
        #   - temporal_consistent=False（有冲突）→ 拒答/追问
        # 只暴露 EvidenceResult 包装，不重设计 EvidenceTable。
        if unique_chunks and settings.evidence_gate_enabled:
            try:
                evidence_table = evidence_organizer.organize(
                    query=req.query,
                    sub_question_chunks=sub_question_chunks,
                    query_type="complex" if len(sub_queries) > 1 else "simple",
                )
                evidence_result = build_evidence_result(evidence_table)
                if ctx:
                    ctx.append(
                        "evidence",
                        {
                            "coverage": evidence_result.coverage,
                            "temporal_consistent": evidence_result.temporal_consistent,
                            "conflicts_count": len(evidence_result.conflicts),
                            "sources_count": len(evidence_result.sources),
                            "coverage_by_year": evidence_result.coverage_by_year,
                        },
                    )
                if evidence_gate_should_refuse(evidence_result, settings.evidence_min_coverage):
                    logger.warning(
                        "evidence.gate.refuse coverage=%.2f threshold=%.2f temporal_consistent=%s",
                        evidence_result.coverage,
                        settings.evidence_min_coverage,
                        evidence_result.temporal_consistent,
                    )
                    reason = (
                        f"证据不足（覆盖度 {evidence_result.coverage:.0%} < 阈值 "
                        f"{settings.evidence_min_coverage:.0%}）"
                    )
                    if not evidence_result.temporal_consistent:
                        reason = f"检测到跨文档/跨年份冲突，需要进一步确认；{reason}"
                    payload = json.dumps(
                        {
                            "phase": "evidence_refused",
                            "reason": reason,
                            "coverage": evidence_result.coverage,
                            "min_coverage": settings.evidence_min_coverage,
                            "temporal_consistent": evidence_result.temporal_consistent,
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: status\ndata: {payload}\n\n"
                    yield (
                        "event: degraded\ndata: "
                        + json.dumps(
                            {
                                "reason": "evidence_gate_refused",
                                "message": reason,
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    yield "event: done\ndata: {}\n\n"
                    return
            except Exception:
                logger.exception("evidence_gate.failed_falling_through")
                # gate 失败不应阻断主流程——降级继续

        # 验证性子问题优先（H 类：错误前提纠偏）
        # 当 chunks 中包含纠正性证据（如"减少"、"下降"等与实际趋势相反的词）时，
        # 确保这些 chunks 不被截断，帮助模型识别并纠正错误前提
        if len(sub_queries) > 1 and len(unique_chunks) > 3:
            correction_keywords = {
                "减少",
                "下降",
                "降低",
                "下滑",
                "萎缩",
                "收缩",
                "实际",
                "趋势",
                "变化",
            }
            correction_chunks = []
            other_chunks = []
            for c in unique_chunks:
                text_snippet = c.text[:500]  # 只检查前 500 字（标题/开头更可能含纠正信息）
                if any(kw in text_snippet for kw in correction_keywords):
                    correction_chunks.append(c)
                else:
                    other_chunks.append(c)
            # 保留前 2 个纠正性 chunk + 其他 chunks
            if correction_chunks:
                unique_chunks = correction_chunks[:2] + other_chunks[: top_k_limit - 2]

        # Context expansion 已禁用：邻居 chunks 经常跨 section 边界
        # （如"合并资产负债表"在"合并利润表"前面），拼接后 c.text 被邻居覆盖，
        # 导致原始 chunk 自身的关键数字（如营业收入）被推到拼接文本的中后段，
        # LLM 在截断后看不到核心数据，反而被邻居内容误导。
        # 数据库里的 chunk 本身已经是结构化好的数据，直接保留原文即可。

        # 先解析文件名；sources 事件延迟到跨文档合并去重之后发出，
        # 否则按文档合并会使 [Source N] 编号整体位移，与 UI 来源卡片对不上
        doc_map = await asyncio.to_thread(_resolve_doc_map, unique_chunks)

        # Cross-doc synthesis: group chunks by document, annotate texts with source
        doc_ids_in_result = list({c.document_id for c in unique_chunks if c.document_id})
        if len(doc_ids_in_result) > 1:
            annotated_texts, doc_groups = cross_doc_synthesizer.synthesize_texts(unique_chunks)
            text_map = {g["document_id"]: at for g, at in zip(doc_groups, annotated_texts)}
            deduped = []
            seen_docs = set()
            for c in unique_chunks:
                if c.document_id in text_map:
                    if c.document_id not in seen_docs:
                        seen_docs.add(c.document_id)
                        c.text = text_map[c.document_id]
                        deduped.append(c)
                else:
                    deduped.append(c)
            unique_chunks = deduped
            for g in doc_groups:
                g["filename"] = doc_map.get(g["document_id"], g["filename"])
            yield f"event: cross_doc\ndata: {json.dumps(doc_groups)}\n\n"

        sources = _build_sources(unique_chunks, doc_map)
        yield f"event: sources\ndata: {json.dumps([s.model_dump() for s in sources])}\n\n"

        if not unique_chunks:
            # 全链路检索空结果：给用户可见信号（前端未识别事件安全忽略），
            # 并在诊断中留痕；LLM 端已由 SYSTEM_ANSWER_TEMPLATE 约束如实告知
            if ctx:
                ctx.record("retrieval_empty", query=req.query)
            # DB-3：检索空结果若叠加 postgres 降级，区分「真无内容」与「故障」——
            # 发 error 事件告知用户服务不可用并终止，不走 LLM 幻觉路径。
            if "postgres" in provider_health.is_degraded():
                yield 'event: error\ndata: {"error":"知识库服务暂时不可用，请稍后重试"}\n\n'
                yield "event: done\ndata: {}\n\n"
                return
            yield "event: no_context\ndata: {}\n\n"

        messages = prompt_builder.build_messages(
            query=req.query,
            history=history,
            summary=summary,
            retrieved_chunks=unique_chunks,
        )

        if ctx:
            ctx.record(
                "topk",
                chunks=[
                    dict(
                        chunk_id=c.chunk_id,
                        document_id=c.document_id,
                        title=c.title,
                        section_path=c.section_path,
                        score=round(c.score, 4),
                        source=sources[i].filename if i < len(sources) else "",
                        text_preview=c.text[:200],
                    )
                    for i, c in enumerate(unique_chunks)
                ],
            )
            total_chars = sum(len(m.get("content", "")) for m in messages)
            ctx.record(
                "prompt",
                system_prompt_chars=len(messages[0].get("content", "")) if messages else 0,
                total_chars=total_chars,
                message_count=len(messages),
                topk_chars=sum(len(c.text) for c in unique_chunks),
            )

        # --- Stream LLM ---
        try:
            await conversation_memory.add_message(
                conv_id,
                "user",
                _pii_safe(req.query),
                status="completed",
                user_id=user_id,
            )
        except Exception:
            # DB-2：用户消息入库失败仅告警——不能因此切流（用户已发起请求，
            # 切流会导致已发出的 sources/cross_doc 事件被丢）
            logging.getLogger(__name__).exception("用户消息入库失败（DB-2 穿透兜底）：流继续")

        yield 'event: status\ndata: {"phase":"thinking","message":"AI 正在思考..."}\n\n'

        full_buffer = ""  # raw accumulation for diagnostics
        stream_start = time.monotonic()
        first_token = True
        chat_degraded = False
        degraded_reply = ""  # 熔断兜底文案：既要流式给用户，也要持久化

        parser = TagStreamParser()

        try:
            async for raw_token in minimax_client.chat_stream(
                messages,
                temperature=req.temperature,
                top_p=req.top_p,
            ):
                if first_token:
                    if ctx:
                        ctx.record(
                            "stream",
                            first_token_ms=round((time.monotonic() - stream_start) * 1000, 1),
                        )
                    first_token = False

                full_buffer += raw_token
                for evt in parser.feed(raw_token):
                    evt_text = evt["text"]
                    if not evt_text:
                        continue
                    if evt["kind"] == "thinking":
                        yield "event: thinking" + _EOL + "data: " + _sse_safe(evt_text) + _EOL2
                    else:
                        yield "event: token" + _EOL + "data: " + _sse_safe(_norm(evt_text)) + _EOL2

        except CircuitOpenError:
            chat_degraded = True
            degraded_reply = "抱歉，AI 服务暂时不可用，请稍后重试。您仍可浏览已上传的文档信息。"
            logging.getLogger(__name__).warning(
                "Chat circuit breaker open, returning degraded response"
            )
            # 旧实现只把兜底文案写库不流式——用户当轮看到空白。现在同步推给前端
            yield "event: token" + _EOL + "data: " + _sse_safe(degraded_reply) + _EOL2

        except GeneratorExit:
            # User interrupted or connection lost
            if parser.answer_text or parser.thinking_text:
                try:
                    await conversation_memory.add_message(
                        conv_id,
                        "assistant",
                        _pii_safe(_norm(parser.answer_text)),
                        thinking_content=_pii_safe(_norm(parser.thinking_text))
                        if parser.thinking_text
                        else None,
                        status="interrupted",
                        user_id=user_id,
                    )
                except Exception:
                    logging.getLogger(__name__).exception("中断消息入库失败（DB-2 穿透兜底）")
            if ctx:
                ctx.update(
                    "stream",
                    total_tokens=len(full_buffer),
                    total_ms=round((time.monotonic() - stream_start) * 1000, 1),
                )
                ctx.save()
            return

        except Exception:
            logging.getLogger(__name__).exception("Chat stream failed")
            chat_degraded = True
            if parser.answer_text or parser.thinking_text:
                try:
                    await conversation_memory.add_message(
                        conv_id,
                        "assistant",
                        _pii_safe(_norm(parser.answer_text)),
                        thinking_content=_pii_safe(_norm(parser.thinking_text))
                        if parser.thinking_text
                        else None,
                        status="interrupted",
                        user_id=user_id,
                    )
                except Exception:
                    logging.getLogger(__name__).exception("中断消息入库失败（DB-2 穿透兜底）")
            if ctx:
                ctx.update(
                    "stream",
                    error="Chat stream failed",
                    total_tokens=len(full_buffer),
                    total_ms=round((time.monotonic() - stream_start) * 1000, 1),
                )
                ctx.save()
            yield 'event: error\ndata: {"error":"生成回复时发生错误，请重试"}\n\n'
            yield "event: done\ndata: {}\n\n"
            return

        # Normal completion —— 排空解析器缓冲（展示与持久化同源于事件流）
        for evt in parser.flush():
            evt_text = evt["text"]
            if not evt_text:
                continue
            if evt["kind"] == "thinking":
                yield "event: thinking" + _EOL + "data: " + _sse_safe(evt_text) + _EOL2
            else:
                yield "event: token" + _EOL + "data: " + _sse_safe(_norm(evt_text)) + _EOL2
        answer_text = _norm(parser.answer_text)
        thinking_text = _norm(parser.thinking_text)
        if not answer_text and degraded_reply:
            answer_text = degraded_reply  # 熔断兜底文案随正常路径持久化
        if answer_text or (thinking_text and not answer_text):
            if not answer_text and thinking_text:
                answer_text = thinking_text
                thinking_text = ""
            try:
                await conversation_memory.add_message(
                    conv_id,
                    "assistant",
                    _pii_safe(answer_text),
                    thinking_content=_pii_safe(thinking_text) if thinking_text else None,
                    status="completed",
                    user_id=user_id,
                )
            except Exception:
                # DB-2：助手回复入库失败仅告警——不切流，degraded 事件仍会发
                logging.getLogger(__name__).exception("助手消息入库失败（DB-2 穿透兜底）")

        degraded_providers = provider_health.is_degraded()
        if settings.degradation_hint_enabled and degraded_providers:
            yield f"event: degraded\ndata: {json.dumps({'providers': degraded_providers})}\n\n"
            if ctx:
                ctx.record("degraded", providers=degraded_providers, chat_degraded=chat_degraded)

        # Emit stream status
        yield f"event: status\ndata: {json.dumps({'phase': 'done', 'thinking_tokens': len(thinking_text), 'answer_tokens': len(answer_text)})}\n\n"

        if ctx:
            ctx.update(
                "stream",
                total_tokens=len(full_buffer),
                total_ms=round((time.monotonic() - stream_start) * 1000, 1),
                thinking_chars=len(thinking_text),
                answer_chars=len(answer_text),
            )
            ctx.save()
        yield "event: done\ndata: {}\n\n"


rag_pipeline = RAGPipeline()

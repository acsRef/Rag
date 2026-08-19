"""Retrieval with vector search + permission filtering + rerank + MMR diversity.

两阶段检索流水线:
  1. 跨编码器重排: cross-encoder (BAAI/bge-reranker-v2-m3) 对粗排结果精排
  2. MMR 多样性: 在精排基础上用 Maximal Marginal Relevance 去冗余,跨文档软惩罚

权限过滤:每个 chunk 在 SQL 层带 `visibility` / `allowed_roles`,按用户角色过滤。
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING

from app.store import pgvector_store
from app.llm.embedding import sf_embedding
from app.llm.rerank import sf_rerank
from app.llm.base import CircuitOpenError
from app.models.schemas import IntentResult, RetrievedChunk
from app.config import settings
from app.core.mmr import mmr_select
from app.core.doc_relation import cross_doc_retriever
from app.llm.base import provider_health

if TYPE_CHECKING:
    from app.core.diagnostics import DiagContext

logger = logging.getLogger(__name__)


async def embed_query_with_fallback(query: str, ctx: "DiagContext | None" = None) -> tuple[list[float] | None, bool]:
    """查询 embedding；熔断/失败时降级为零向量（BM25-only）。

    返回 (embedding, degraded)。embedding 为 None 表示纯向量模式
    （hybrid_search_enabled=False）下 embedding 失败——零向量余弦排序未定义，
    调用方应返回空结果并交由上层兜底。

    ctx 为可选 DiagContext：熔断时记录与原 retrieve 流程一致的诊断
    （track_error('embedding', 'CircuitOpenError', ...)）；普通异常只留
    warning 日志、不写诊断（保持重构前语义）。
    """
    try:
        return await sf_embedding.embed(query), False
    except CircuitOpenError:
        logger.warning("embed_query.embedding.degraded — circuit open, using zero-vector (BM25-only fallback)")
        if ctx:
            ctx.track_error("embedding", "CircuitOpenError", "embedding circuit breaker open, BM25-only", degraded=True)
    except Exception as exc:
        logger.warning("embed_query.embedding.failed — embedding failed (%s), using zero-vector (BM25-only fallback)", exc)
    if not settings.hybrid_search_enabled:
        return None, True
    return [0.0] * settings.embedding_dimension, True


def _search_kb(
    kb_id: str,
    query_emb: list[float],
    query: str,
    user_role_ids: list[int] | None,
    can_read_all: bool,
    top_k: int,
    user_id: str = "",
    document_ids: list[str] | None = None,
) -> list[dict]:
    fn = pgvector_store.hybrid_search if settings.hybrid_search_enabled else pgvector_store.search
    kwargs: dict = dict(
        kb_ids=[kb_id],
        embedding=query_emb,
        user_role_ids=user_role_ids,
        can_read_all=can_read_all,
        top_k=top_k,
        user_id=user_id,
    )
    if settings.hybrid_search_enabled:
        kwargs.update(query=query, fetch_k=settings.hybrid_search_top_k, rrf_k=settings.hybrid_rrf_k,
                       enable_question_channel=settings.question_channel_enabled)
        if document_ids:
            kwargs["document_ids"] = document_ids
    else:
        # Pure vector search also supports document_ids
        if document_ids:
            kwargs["document_ids"] = document_ids
    # DB 熔断入口闸门：postgres OPEN 时直接返回 []（不再撞库导致 SSE 挂起）
    if not provider_health.get("postgres").allow_request():
        return []
    try:
        return fn(**kwargs)
    except Exception:
        logger.exception("Search failed for kb_id=%s", kb_id)
        provider_health.get("postgres").on_failure()   # DB-1：计入熔断，pipeline 末尾 degraded 事件自动暴露
        return []


async def _collect_results(
    kb_ids: list[str],
    query_emb: list[float],
    query: str,
    user_role_ids: list[int] | None,
    can_read_all: bool,
    top_k: int,
    seen_ids: set[str],
    results: list[dict],
    user_id: str = "",
    document_ids: list[str] | None = None,
):
    """设计审查 P1-7：并行检索各 KB，再在主协程归并去重。

    旧实现逐 KB 串行调 _search_kb（每 KB 最多 4 次顺序 DB 查询）。现在
    asyncio.gather 并行各 KB（各搜索经 to_thread 跑在线程池）；去重/标注
    在事件循环内由 gather 顺序收集后统一处理，天然线程竞态安全。

    document_ids: if provided, restrict search to chunks in these documents only
    (two-stage retrieval: stage 2 uses document filter from stage 1).
    """
    if not kb_ids:
        return
    per_kb = await asyncio.gather(
        *(asyncio.to_thread(
            _search_kb, kb_id, query_emb, query, user_role_ids, can_read_all, top_k,
            user_id=user_id, document_ids=document_ids) for kb_id in kb_ids),
        return_exceptions=True,
    )
    for kb_id, chunks in zip(kb_ids, per_kb):
        if isinstance(chunks, BaseException):
            # 单个 KB 检索失败不应拖垮整轮（_search_kb 内部已计入熔断）
            logger.warning("retrieve.kb_failed kb_id=%s err=%r", kb_id, chunks)
            continue
        for c in chunks:
            if c["chunk_id"] not in seen_ids:
                seen_ids.add(c["chunk_id"])
                c["kb_id"] = kb_id
                results.append(c)


# ── Section-aware boost ──────────────────────────────────

# Section 类型定义：(查询关键词, 权威 section 模式, boost 倍数)
# 原理：向量/BM25 检索偏向叙述性文本，表格数据（如"主要会计数据"）分数偏低。
# 通过 section 权威性 boost 把权威 section 的 chunk 推到最前面。
_SECTION_RULES = [
    {
        "query_keywords": {
            "营收", "营业收入", "营业总收入", "利润", "净利润", "归母",
            "资产", "总资产", "负债", "现金流", "每股", "分红", "股利",
            "毛利率", "净利率", "净资产", "负债率", "权益", "公积金",
            "基本每股收益", "稀释每股收益", "单位", "金额单位", "千元",
        },
        "section_patterns": [
            "主要会计数据", "主要财务指标",
        ],
        "boost": 5.0,  # 强力 boost：核心汇总数据表
    },
    {
        "query_keywords": {
            "研发", "资本化", "费用化", "研发投入",
        },
        "section_patterns": [
            "研发投入情况表", "研发投入",
        ],
        "boost": 5.0,  # 研发表格
    },
    {
        "query_keywords": {
            "营收", "营业收入", "利润", "资产", "负债", "现金流",
            "每股", "分红", "股利",
        },
        "section_patterns": [
            "合并利润表", "合并资产负债表", "合并现金流量表",
            "利润表", "资产负债表", "现金流量表", "所有者权益变动表",
        ],
        "boost": 3.0,  # 财务报表
    },
    {
        "query_keywords": {
            "员工", "在职", "人数", "职工",
        },
        "section_patterns": [
            "在职员工", "员工情况", "员工",
        ],
        "boost": 3.0,
    },
    {
        "query_keywords": {
            "持股", "控股", "子公司", "参股",
        },
        "section_patterns": [
            "主要控股", "子公司", "参股公司",
        ],
        "boost": 3.0,
    },
    {
        "query_keywords": {
            "战略", "业务", "市场", "竞争", "行业", "产品", "客户",
            "渠道", "海外", "国际", "国内", "创新", "趋势",
            "经营", "讨论", "分析",
        },
        "section_patterns": [
            "管理层讨论与分析", "经营情况讨论与分析",
        ],
        "boost": 2.0,  # 业务分析
    },
    {
        "query_keywords": {
            "董事", "监事", "高管", "薪酬", "股东", "股权", "治理",
            "签字", "会计师", "审计", "委员会",
        },
        "section_patterns": [
            "公司治理", "董事", "监事", "高级管理人员", "股东信息",
        ],
        "boost": 2.0,
    },
]


def _boost_by_section_type(results: list[dict], query: str) -> list[dict]:
    """根据 query 关键词给权威 section 的 chunks 大幅加分。

    匹配 section_path 的最后一级（叶子节点），避免父级路径误匹配。
    多规则叠加：如果一个 chunk 匹配多个规则，boost 倍数累积。
    """
    if not results or not query:
        return results

    boosted = False
    for r in results:
        full_path = r.get("section_path", "") or ""
        leaf = full_path.rsplit(">", 1)[-1].strip() if ">" in full_path else full_path
        total_boost = 1.0

        for rule in _SECTION_RULES:
            if not any(kw in query for kw in rule["query_keywords"]):
                continue
            if any(pat in leaf for pat in rule["section_patterns"]):
                total_boost *= rule["boost"]
                boosted = True

        if total_boost > 1.0:
            r["score"] = r.get("score", 0) * total_boost

    if boosted:
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        logger.debug("section_boost applied query=%s", query[:40])

    return results


# 权威 section 列表 — 这些 section 的数据最重要，需要保证被检索到
_AUTHORITATIVE_SECTIONS = [
    # 核心财务汇总表
    "主要会计数据", "主要财务指标",
    # 研发相关
    "研发投入情况表", "研发投入",
    # 利润表相关
    "利润表", "合并利润表", "母公司利润表",
    # 资产负债相关
    "资产负债表", "合并资产负债表", "母公司资产负债表",
    # 现金流相关
    "现金流量表", "合并现金流量表",
    # 员工相关
    "在职员工", "员工情况",
    # 分红相关
    "利润分配", "分红",
]


def _supplement_authoritative_sections(
    results: list[dict],
    query: str,
    kb_ids: list[str],
    user_role_ids: list[int] | None,
    can_read_all: bool,
    user_id: str,
) -> list[dict]:
    """补充检索：确保权威 section 的正确 chunks 在候选中。

    问题：
    1. BM25 ts_rank 偏向叙述性文本，表格数据（如"主要会计数据"）经常被挤出 top-K
    2. 同一 section path 下可能有多个 chunk（正确的表格 + 无关的文本），
       主检索可能只拿到无关的那个

    解决：对财务类查询，定向 BM25 检索权威 section 的 chunks，
    确保最高分的正确 chunk 在候选中。
    """
    if not query:
        return results

    # 检查 query 是否涉及权威 section 的数据类型
    financial_keywords = {
        # 财务数据
        "营收", "营业收入", "营业总收入", "利润", "净利润", "归母",
        "资产", "总资产", "负债", "现金流", "每股", "分红", "股利",
        "毛利率", "净利率", "净资产", "负债率", "权益", "公积金",
        "基本每股收益", "稀释每股收益",
        # 研发
        "研发", "资本化", "费用化",
        # 表格/单位
        "单位", "金额单位", "千元", "万元", "亿元",
        # 员工
        "员工", "在职", "人数",
        # 投资/持股
        "持股", "控股", "子公司",
    }
    if not any(kw in query for kw in financial_keywords):
        return results

    existing_ids = {r.get("chunk_id") for r in results}
    import re as _re
    query_years = set(_re.findall(r'(20\d{2})', query))

    # Step 1: 对已有结果中的权威 chunks 加分（防止低分被 rerank/MMR 淘汰）
    boosted_existing = 0
    for r in results:
        full_path = r.get("section_path", "") or ""
        leaf = full_path.rsplit(">", 1)[-1].strip() if ">" in full_path else full_path
        if not any(sec in leaf for sec in _AUTHORITATIVE_SECTIONS):
            continue
        # 年份匹配
        if query_years:
            chunk_years = set(_re.findall(r'(20\d{2})', full_path))
            if not (query_years & chunk_years):
                continue
        # 权威 chunk 且年份匹配 → 加分
        r["score"] = r.get("score", 0) * 3.0
        boosted_existing += 1

    if boosted_existing > 0:
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        logger.info("section_supplement boosted_existing=%d query=%s",
                     boosted_existing, query[:40])

    # Step 2: 定向补充检索（添加主检索中缺失的权威 chunks）
    try:
        from app.store.pgvector_store import bm25_search
        supplementary = bm25_search(
            kb_ids=kb_ids,
            query=query,
            user_role_ids=user_role_ids,
            can_read_all=can_read_all,
            top_k=50,
            user_id=user_id,
        )

        added = 0
        for r in supplementary:
            if r.get("chunk_id") in existing_ids:
                continue
            full_path = r.get("section_path", "") or ""
            leaf = full_path.rsplit(">", 1)[-1].strip() if ">" in full_path else full_path
            if not any(sec in leaf for sec in _AUTHORITATIVE_SECTIONS):
                continue
            if query_years:
                chunk_years = set(_re.findall(r'(20\d{2})', full_path))
                if not (query_years & chunk_years):
                    continue
            results.append(r)
            existing_ids.add(r["chunk_id"])
            added += 1
            if added >= 2:
                break

        if added > 0:
            results.sort(key=lambda x: x.get("score", 0), reverse=True)
            logger.info("section_supplement added=%d query=%s", added, query[:40])

    except Exception:
        logger.exception("section_supplement failed")

    return results


class RetrievalEngine:
    async def retrieve(
        self,
        query: str,
        intent: IntentResult | None,
        user_role_ids: list[int] | None = None,
        can_read_all: bool = False,
        ctx=None,  # DiagContext, injected from pipeline.py
        user_id: str = "",
        use_two_stage: bool = False,
    ) -> list[RetrievedChunk]:
        top_k = settings.vector_search_top_k
        round_data: dict | None = None
        if ctx is not None:
            round_data = {"sub_query": query}

        # Milestone 1: 检索入口
        t_total = time.monotonic()
        if intent and intent.matches:
            target_kb_ids = [m.kb_id for m in intent.matches]
        else:
            # 同步 DB 调用必须 to_thread，否则阻塞事件循环
            target_kb_ids = await asyncio.to_thread(pgvector_store.list_kb_ids)
        logger.info(
            "retrieve.start query_len=%d kb_target_count=%d",
            len(query), len(target_kb_ids),
        )

        if round_data is not None:
            round_data["target_kb_ids"] = target_kb_ids

        t_embed = time.monotonic()
        query_emb, embedding_degraded = await embed_query_with_fallback(query, ctx)
        embed_elapsed = (time.monotonic() - t_embed) * 1000
        if query_emb is None:
            # 纯向量模式 + embedding 失败：零向量余弦排序未定义，宁可空结果触发上层兜底
            logger.warning("retrieve.pure_vector_degraded — embedding failed with hybrid off, returning []")
            if ctx:
                ctx.track_error("embedding", "ZeroVectorNoHybrid",
                                "pure-vector mode with failed embedding returns []", degraded=True)
            return []
        # Milestone 2: query 嵌入完成(DEBUG,因为正常路径会调无数次)
        logger.debug(
            "retrieve.embedded dim=%d elapsed_ms=%.1f",
            len(query_emb), embed_elapsed,
        )

        if round_data is not None:
            round_data["embedding"] = {
                "dims": len(query_emb),
                "elapsed_ms": round(embed_elapsed, 1),
                "degraded": embedding_degraded,
            }

        seen_ids: set[str] = set()
        results: list[dict] = []

        # Two-stage retrieval: first find relevant documents, then search within them
        doc_filter: list[str] | None = None
        if use_two_stage:
            t_stage1 = time.monotonic()
            try:
                doc_filter = await asyncio.to_thread(
                    pgvector_store.pre_retrieve_documents,
                    query_emb, target_kb_ids,
                    5,  # top_k_docs
                    0.3,  # threshold
                )
                stage1_elapsed = (time.monotonic() - t_stage1) * 1000
                logger.info(
                    "retrieve.two_stage stage1_docs=%d elapsed_ms=%.1f",
                    len(doc_filter), stage1_elapsed,
                )
                if round_data is not None:
                    round_data.setdefault("two_stage", {})["stage1"] = {
                        "doc_count": len(doc_filter),
                        "doc_ids": doc_filter[:5],
                        "elapsed_ms": round(stage1_elapsed, 1),
                    }
            except Exception:
                logger.exception("two_stage.stage1_failed, falling back to single-stage")
                doc_filter = None

        await _collect_results(
            target_kb_ids, query_emb, query,
            user_role_ids, can_read_all, top_k, seen_ids, results,
            user_id, document_ids=doc_filter,
        )

        if intent and intent.matches:
            min_confidence = min(m.score for m in intent.matches)
            if len(results) < top_k and min_confidence < 0.6:
                # 同步 DB 调用必须 to_thread
                all_kb_ids = await asyncio.to_thread(pgvector_store.list_kb_ids)
                fallback = [k for k in all_kb_ids if k not in target_kb_ids]
                await _collect_results(
                    fallback, query_emb, query,
                    user_role_ids, can_read_all, top_k, seen_ids, results,
                    user_id, document_ids=doc_filter,
                )

        # 补充检索：确保权威 section 的 chunks 在候选中
        # BM25 ts_rank 偏向叙述性文本，表格数据（如"主要会计数据"）关键词稀疏
        # 经常被挤出 top-K，导致 LLM 拿到错误 section 的数据
        results = _supplement_authoritative_sections(
            results, query, target_kb_ids,
            user_role_ids, can_read_all, user_id,
        )

        results.sort(key=lambda x: x["score"], reverse=True)
        candidate_k = settings.mmr_candidate_k if settings.mmr_enabled else top_k
        results = results[:candidate_k]
        # Milestone 3: 粗排+重排序前候选汇总
        logger.info(
            "retrieve.candidates count=%d elapsed_ms=%.1f",
            len(results), (time.monotonic() - t_total) * 1000,
        )

        if round_data is not None:
            round_data["search_candidates"] = [
                {"chunk_id": r["chunk_id"], "kb_id": r.get("kb_id", ""), "score": round(r["score"], 4), "title": r.get("title", "")}
                for r in results[:10]  # 只记录前10个避免文件过大
            ]

        # Save original chunk IDs before cross-doc inflates scores
        original_chunk_ids = {r["chunk_id"] for r in results}

        # -- Cross-doc retrieval (three-channel jump) --
        cross_doc_extra_count = 0
        try:
            # retrieve_sync 内部全同步 DB 调用，必须 to_thread，否则阻塞事件循环
            extra = await asyncio.to_thread(
                cross_doc_retriever.retrieve_sync,
                query, query_emb, target_kb_ids,
                results, user_role_ids, can_read_all, user_id,
            )
            if extra:
                cross_doc_extra_count = len(extra)
                # 归一化映射：附加 chunk 落在最强直连分的 70%~100% 区间，
                # 保留邻居间次序，最终位置交给 rerank/MMR 决定——
                # 旧的 min(score, max_rrf) 把通道分（0–1）压到 RRF 量纲（~0.01），
                # 候选截断后附加 chunk 全部沉底，三通道形同虚设
                max_neighbor = max(c["score"] for c in extra)
                max_original = max((r["score"] for r in results), default=0.3)
                for c in extra:
                    rel = c["score"] / max_neighbor if max_neighbor else 1.0
                    c["score"] = max_original * (0.7 + 0.3 * rel)
                results.extend(extra)
                results.sort(key=lambda x: x["score"], reverse=True)
                results = results[:candidate_k]
                seen_ids.update(c["chunk_id"] for c in extra)
                logger.info("cross_doc.extra_added count=%d", len(extra))
        except Exception:
            logger.exception("cross_doc.retrieve_failed")

        if round_data is not None:
            round_data["cross_doc"] = {
                "extra_count": cross_doc_extra_count,
                "candidate_k": candidate_k,
            }

        rerank_before_count = len(results) if results else 0
        rerank_degraded = False
        if results:
            texts = [r["text"] for r in results]
            try:
                t_rerank = time.monotonic()
                reranked = await sf_rerank.rerank(query, texts)
                if not reranked:
                    # rerank 失败被吞成 []，与"无结果"不可区分——至少留条告警线索
                    logger.warning(
                        "retrieve.rerank.empty — reranker returned nothing for %d candidates, keeping search order",
                        len(texts),
                    )
                if reranked:
                    # 降级判断: rerank 分数无明显区分度时，跳过 reordering
                    # (短查询如"绿色闪烁"常导致 reranker 全给 0 分，
                    #  此时重排序会随机打乱正确结果，被后续 MMR 淘汰)
                    rerank_scores = [r.get("relevance_score", 0) for r in reranked]
                    max_score = max(rerank_scores)
                    min_score = min(rerank_scores)
                    if max_score - min_score > 0.001:
                        reranked_ids = [r["index"] for r in reranked if 0 <= r["index"] < len(results)]
                        reordered = [results[i] for i in reranked_ids]
                        # reranker 未返回的候选按原序追加——不静默丢弃
                        returned = set(reranked_ids)
                        reordered += [r for i, r in enumerate(results) if i not in returned]
                        results = reordered
                    else:
                        logger.debug("retrieve.rerank.skip_degraded query_len=%d candidates=%d "
                                     "score_range=[%.4f, %.4f]",
                                     len(query), len(reranked), min_score, max_score)
                        # Rerank 无区分度: 丢弃 cross-doc 额外 chunk,恢复原始搜索排序
                        results = [r for r in results if r["chunk_id"] in original_chunk_ids]
                        results.sort(key=lambda x: x["score"], reverse=True)
                rerank_elapsed = (time.monotonic() - t_rerank) * 1000
                # Milestone 4: 跨编码器重排(DEBUG,每次问答都打)
                logger.debug(
                    "retrieve.reranked from=%d to=%d elapsed_ms=%.1f",
                    rerank_before_count, len(results), rerank_elapsed,
                )

                if round_data is not None:
                    rerank_scores = [r.get("relevance_score", 0) for r in reranked]
                    top_score = max(rerank_scores) if rerank_scores else 0
                    bottom_score = min(rerank_scores) if rerank_scores else 0
                    score_range = f"{bottom_score:.4f} ~ {top_score:.4f}"
                    round_data["rerank"] = {
                        "before_count": rerank_before_count,
                        "after_count": len(results),
                        "elapsed_ms": round(rerank_elapsed, 1),
                        "score_range": score_range,
                        "rerank_scores": [round(s, 4) for s in rerank_scores[:10]],
                        "degraded": False,
                    }
            except CircuitOpenError:
                rerank_degraded = True
                logger.warning("retrieve.rerank.degraded — circuit open, skipping rerank")
                if ctx:
                    ctx.track_error("rerank", "CircuitOpenError", "rerank circuit breaker open, skipped", degraded=True)
                if round_data is not None:
                    round_data["rerank"] = {
                        "before_count": rerank_before_count,
                        "after_count": len(results),
                        "elapsed_ms": 0,
                        "degraded": True,
                    }
            except Exception:
                logger.exception("Rerank failed for query=%s", query)
                if round_data is not None:
                    round_data["rerank"] = {"before_count": rerank_before_count, "error": True}
                rerank_degraded = True  # treat as degraded

        if settings.mmr_enabled and len(results) > settings.rerank_top_k:
            t_mmr = time.monotonic()
            before_mmr = results[:]
            results = mmr_select(
                candidates=results,
                lambda_=settings.mmr_lambda,
                top_k=settings.rerank_top_k,
                max_per_doc=settings.mmr_max_per_doc,
                doc_penalty=settings.mmr_doc_penalty,
            )
            mmr_elapsed = (time.monotonic() - t_mmr) * 1000
            # Milestone 5: MMR 多样性筛选完成(INFO,因为是关键阶段)
            logger.info(
                "retrieve.final count=%d lambda=%.2f elapsed_ms=%.1f total_elapsed_ms=%.1f",
                len(results), settings.mmr_lambda,
                mmr_elapsed,
                (time.monotonic() - t_total) * 1000,
            )

            if round_data is not None:
                rejected_ids = [c["chunk_id"] for c in before_mmr if c["chunk_id"] not in {r["chunk_id"] for r in results}]
                round_data["mmr"] = {
                    "selected_count": len(results),
                    "rejected_count": len(rejected_ids),
                    "rejected_chunk_ids": rejected_ids[:20],  # 最多记录20个被剔除的
                    "lambda": settings.mmr_lambda,
                    "max_per_doc": settings.mmr_max_per_doc,
                    "doc_penalty": settings.mmr_doc_penalty,
                    "elapsed_ms": round(mmr_elapsed, 1),
                }
        else:
            results = results[:settings.rerank_top_k]
            logger.info(
                "retrieve.final count=%d (mmr skipped) total_elapsed_ms=%.1f",
                len(results), (time.monotonic() - t_total) * 1000,
            )

        for r in results:
            r.setdefault("document_id", "")

        # 年份注入：按 document_id 查文件名提取年份，注入到每条结果
        # 显式字段，不依赖路径猜测；非年报类文档（无年份）则 year 为空
        doc_ids = {r["document_id"] for r in results if r["document_id"]}
        year_map: dict[str, str] = {}
        if doc_ids:
            try:
                from app.store.db import get_db_ctx, Document
                with get_db_ctx() as session:
                    rows = session.query(Document.document_id, Document.filename).filter(
                        Document.document_id.in_(doc_ids)).all()
                    from app.ingestion.indexer import _extract_year_from_filename
                    for doc_id, filename in rows:
                        year_map[doc_id] = _extract_year_from_filename(filename) or ""
            except Exception:
                logger.exception("year_enrichment_failed")
        for r in results:
            r["year"] = year_map.get(r["document_id"], "")

        # Section-aware boost: 根据 query 关键词给特定 section 的 chunks 加分
        results = _boost_by_section_type(results, query)

        if round_data is not None:
            round_data["total_elapsed_ms"] = round((time.monotonic() - t_total) * 1000, 1)
            ctx.append("retrieval", round_data)

        return [RetrievedChunk(**r) for r in results]


retrieval_engine = RetrievalEngine()

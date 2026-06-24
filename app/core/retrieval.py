"""Retrieval with vector search + permission filtering + rerank + MMR diversity.

两阶段检索流水线:
  1. 跨编码器重排: cross-encoder (BAAI/bge-reranker-v2-m3) 对粗排结果精排
  2. MMR 多样性: 在精排基础上用 Maximal Marginal Relevance 去冗余,跨文档软惩罚

权限过滤:每个 chunk 在 SQL 层带 `visibility` / `allowed_roles`,按用户角色过滤。
"""
import logging
import time

from app.store import pgvector_store
from app.llm.embedding import sf_embedding
from app.llm.rerank import sf_rerank
from app.models.schemas import IntentResult, RetrievedChunk
from app.config import settings
from app.core.mmr import mmr_select

logger = logging.getLogger(__name__)


def _search_kb(
    kb_id: str,
    query_emb: list[float],
    query: str,
    user_role_ids: list[int] | None,
    can_read_all: bool,
    top_k: int,
) -> list[dict]:
    fn = pgvector_store.hybrid_search if settings.hybrid_search_enabled else pgvector_store.search
    kwargs: dict = dict(
        kb_ids=[kb_id],
        embedding=query_emb,
        user_role_ids=user_role_ids,
        can_read_all=can_read_all,
        top_k=top_k,
    )
    if settings.hybrid_search_enabled:
        kwargs.update(query=query, fetch_k=settings.hybrid_search_top_k, rrf_k=settings.hybrid_rrf_k)
    try:
        return fn(**kwargs)
    except Exception:
        logger.exception("Search failed for kb_id=%s", kb_id)
        return []


def _collect_results(
    kb_ids: list[str],
    query_emb: list[float],
    query: str,
    user_role_ids: list[int] | None,
    can_read_all: bool,
    top_k: int,
    seen_ids: set[str],
    results: list[dict],
):
    for kb_id in kb_ids:
        chunks = _search_kb(kb_id, query_emb, query, user_role_ids, can_read_all, top_k)
        for c in chunks:
            if c["chunk_id"] not in seen_ids:
                seen_ids.add(c["chunk_id"])
                c["kb_id"] = kb_id
                results.append(c)


class RetrievalEngine:
    def retrieve(
        self,
        query: str,
        intent: IntentResult | None,
        user_role_ids: list[int] | None = None,
        can_read_all: bool = False,
    ) -> list[RetrievedChunk]:
        top_k = settings.vector_search_top_k

        # Milestone 1: 检索入口
        t_total = time.monotonic()
        if intent and intent.matches:
            target_kb_ids = [m.kb_id for m in intent.matches]
        else:
            target_kb_ids = pgvector_store.list_kb_ids()
        logger.info(
            "retrieve.start query_len=%d kb_target_count=%d",
            len(query), len(target_kb_ids),
        )

        t_embed = time.monotonic()
        query_emb = sf_embedding.embed(query)
        # Milestone 2: query 嵌入完成(DEBUG,因为正常路径会调无数次)
        logger.debug(
            "retrieve.embedded dim=%d elapsed_ms=%.1f",
            len(query_emb), (time.monotonic() - t_embed) * 1000,
        )

        seen_ids: set[str] = set()
        results: list[dict] = []

        _collect_results(target_kb_ids, query_emb, query, user_role_ids, can_read_all, top_k, seen_ids, results)

        if intent and intent.matches:
            min_confidence = min(m.score for m in intent.matches)
            if len(results) < top_k and min_confidence < 0.6:
                all_kb_ids = pgvector_store.list_kb_ids()
                fallback = [k for k in all_kb_ids if k not in target_kb_ids]
                _collect_results(fallback, query_emb, query, user_role_ids, can_read_all, top_k, seen_ids, results)

        results.sort(key=lambda x: x["score"], reverse=True)
        candidate_k = settings.mmr_candidate_k if settings.mmr_enabled else top_k
        results = results[:candidate_k]
        # Milestone 3: 粗排+重排序前候选汇总
        logger.info(
            "retrieve.candidates count=%d elapsed_ms=%.1f",
            len(results), (time.monotonic() - t_total) * 1000,
        )

        if results:
            texts = [r["text"] for r in results]
            try:
                t_rerank = time.monotonic()
                reranked = sf_rerank.rerank(query, texts)
                reranked_ids = [r["index"] for r in reranked if 0 <= r["index"] < len(results)]
                results = [results[i] for i in reranked_ids]
                # Milestone 4: 跨编码器重排(DEBUG,每次问答都打)
                logger.debug(
                    "retrieve.reranked from=%d to=%d elapsed_ms=%.1f",
                    len(texts), len(results), (time.monotonic() - t_rerank) * 1000,
                )
            except Exception:
                logger.exception("Rerank failed for query=%s", query)

        if settings.mmr_enabled and len(results) > settings.rerank_top_k:
            t_mmr = time.monotonic()
            results = mmr_select(
                candidates=results,
                lambda_=settings.mmr_lambda,
                top_k=settings.rerank_top_k,
                max_per_doc=settings.mmr_max_per_doc,
                doc_penalty=settings.mmr_doc_penalty,
            )
            # Milestone 5: MMR 多样性筛选完成(INFO,因为是关键阶段)
            logger.info(
                "retrieve.final count=%d lambda=%.2f elapsed_ms=%.1f total_elapsed_ms=%.1f",
                len(results), settings.mmr_lambda,
                (time.monotonic() - t_mmr) * 1000,
                (time.monotonic() - t_total) * 1000,
            )
        else:
            results = results[:settings.rerank_top_k]
            logger.info(
                "retrieve.final count=%d (mmr skipped) total_elapsed_ms=%.1f",
                len(results), (time.monotonic() - t_total) * 1000,
            )

        for r in results:
            r.setdefault("document_id", "")

        return [RetrievedChunk(**r) for r in results]


retrieval_engine = RetrievalEngine()

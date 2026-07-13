"""Cross-document relation retrieval.

Three-channel jump strategy:
  1. TF-IDF relation edges stored in doc_relations table (cosine threshold).
  2. Query low-TF keyword match against target doc entity sets.
  3. Doc-level embedding cosine (N>1000, uses pgvector).

Ingestion builds the relation matrix; queries cost only DB lookups + set ops
(no LLM, no embedding API calls).

Design notes:
  - doc_relations stores edges bidirectionally (A->B AND B->A) to simplify
    lookups at the cost of 2x row count.
  - ON DELETE CASCADE on source_doc cleans outgoing edges when a doc is
    deleted. Incoming edges (where the deleted doc is target_doc) must be
    cleaned manually via delete_doc_relations_by_doc_id.
  - This module is async-def but internally uses ONLY synchronous
    SQLAlchemy calls. It does NOT call any LLM, so await-ing these methods
    will NOT block the event loop. If you add LLM calls inside, wrap them
    in asyncio.to_thread.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any

import jieba
import numpy as np

from app.config import settings
from app.store import pgvector_store

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

_STOPWORDS: frozenset[str] = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "与", "及", "等", "或", "但", "而", "且", "如果", "因为", "所以",
    "可以", "能够", "应该", "必须", "可能", "已经", "还", "更", "最",
    "被", "把", "对", "从", "向", "于", "以", "为", "由",
    "the", "a", "an", "of", "in", "to", "is", "for", "on", "and",
    "or", "but", "with", "as", "at", "by", "from", "that", "this",
    "are", "was", "were", "been", "be", "have", "has", "had", "do",
    "does", "did", "will", "would", "can", "could", "may", "might",
    "shall", "should", "about", "into", "through", "during", "before",
    "after", "above", "below", "up", "down", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there",
    "when", "where", "why", "how", "all", "each", "every", "both",
    "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very",
})

# Tunables — promote to Settings if these need operator-facing knobs.
_MIN_TERM_FREQ = 2
_MAX_TERMS_PER_DOC = 200
_COMPLEMENTARY_THRESHOLD = 0.3  # cosine >= this -> complementary candidate
_DUPLICATE_JACCARD_LIMIT = 0.5  # jaccard >= this -> mark as duplicate, not complementary
_QUERY_KEYWORD_MATCH_RATIO = 0.2  # channel-2 minimum coverage
_CH2_MAX_CANDIDATES = 200  # channel-2 max docs to evaluate per query
_MAX_NEIGHBORS_PER_QUERY = 5  # cap on cross-doc neighbor count


# ── Tokenization ─────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Tokenize text via jieba; drop stopwords and single-char tokens."""
    return [
        w for w in jieba.cut(text)
        if w.strip() and w not in _STOPWORDS and len(w) > 1
    ]


# ── Unified chunk accessor (handles RetrievedChunk objects and dicts) ───

def _chunk_attr(chunk: Any, key: str, default: str = "") -> str:
    """Read an attribute from a RetrievedChunk object or a dict uniformly."""
    if isinstance(chunk, dict):
        return chunk.get(key, default)
    return getattr(chunk, key, default)


# ── TF-IDF feature extractor ─────────────────────────────────────────────

class TfidfFeatureExtractor:
    """Compute TF-IDF weighted term lists and cosine between two documents.

    Stage 1 (N<1000): pure in-memory TF-IDF.
    Stage 2 (N>1000): swap to embedding pool via doc_embeddings table.
    """

    def __init__(self, max_terms: int = _MAX_TERMS_PER_DOC) -> None:
        self.max_terms = max_terms
        self._global_df: dict[str, int] | None = None
        self._total_docs: int = 0

    def refresh_global_stats(
        self, all_docs_entities: dict[str, list[tuple[str, int]]]
    ) -> None:
        """Compute global document frequency across all docs.

        NOTE: Loads ALL document entities into memory. For N > 5000,
        consider a global DF cache updated incrementally rather than
        full recompute on every document update.
        """
        df: dict[str, int] = {}
        for entities in all_docs_entities.values():
            seen_in_doc: set[str] = set()
            for entity, freq in entities:
                if freq < _MIN_TERM_FREQ or entity in seen_in_doc:
                    continue
                seen_in_doc.add(entity)
                df[entity] = df.get(entity, 0) + 1
        self._global_df = df
        self._total_docs = len(all_docs_entities)

    def _idf(self, term: str) -> float:
        if self._global_df is None or self._total_docs == 0:
            return 1.0
        df = self._global_df.get(term, 1)
        # Smoothed IDF — avoids log(0) and dampens zero-frequency terms.
        return math.log((1 + self._total_docs) / (1 + df)) + 1

    def extract(self, chunks: list[dict]) -> list[tuple[str, int]]:
        """Extract top-K TF terms from a document's chunks."""
        text = " ".join(c.get("text", "") for c in chunks)
        freq: dict[str, int] = {}
        for w in _tokenize(text):
            freq[w] = freq.get(w, 0) + 1
        terms = [(w, f) for w, f in freq.items() if f >= _MIN_TERM_FREQ]
        terms.sort(key=lambda x: x[1], reverse=True)
        return terms[:self.max_terms]

    def extract_from_text(self, text: str) -> list[tuple[str, int]]:
        """Extract from raw text. Queries are typically short; no min-freq filter."""
        freq: dict[str, int] = {}
        for w in _tokenize(text):
            freq[w] = freq.get(w, 0) + 1
        terms = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return terms[:self.max_terms]

    def cosine_between(
        self,
        a_terms: list[tuple[str, int]],
        b_terms: list[tuple[str, int]],
    ) -> float:
        """TF-IDF weighted cosine similarity between two documents."""
        a_dict = dict(a_terms)
        b_dict = dict(b_terms)
        all_terms = set(a_dict) | set(b_dict)
        if not all_terms:
            return 0.0

        dot = 0.0
        a_norm_sq = 0.0
        b_norm_sq = 0.0
        for term in all_terms:
            a_tf = a_dict.get(term, 0)
            b_tf = b_dict.get(term, 0)
            if a_tf == 0 and b_tf == 0:
                continue
            idf = self._idf(term)
            a_w = a_tf * idf
            b_w = b_tf * idf
            dot += a_w * b_w
            a_norm_sq += a_w * a_w
            b_norm_sq += b_w * b_w

        a_norm = math.sqrt(a_norm_sq)
        b_norm = math.sqrt(b_norm_sq)
        if a_norm == 0 or b_norm == 0:
            return 0.0
        return dot / (a_norm * b_norm)

    def jaccard_entities(
        self,
        a_terms: list[tuple[str, int]],
        b_terms: list[tuple[str, int]],
    ) -> float:
        """Entity-level Jaccard similarity (ignores term frequency)."""
        a_set = {t for t, _ in a_terms}
        b_set = {t for t, _ in b_terms}
        union = a_set | b_set
        if not union:
            return 0.0
        return len(a_set & b_set) / len(union)

    def classify_relation(self, cosine: float, jaccard: float) -> str:
        """Classify as complementary vs duplicate.

        - cosine < threshold: 'unknown'
        - cosine >= threshold AND jaccard >= duplicate limit: 'duplicate' (same info)
        - cosine >= threshold AND jaccard < duplicate limit: 'complementary'
        """
        if cosine < _COMPLEMENTARY_THRESHOLD:
            return "unknown"
        if jaccard >= _DUPLICATE_JACCARD_LIMIT:
            return "duplicate"
        return "complementary"


# ── Document relation builder ────────────────────────────────────────────

class DocRelationBuilder:
    """Build and persist inter-document relation edges.

    Triggered after each document index/update and once on startup.
    """

    def __init__(self) -> None:
        self._extractor = TfidfFeatureExtractor()

    def update_for_document(self, doc_id: str) -> None:
        """Recompute relations involving this document."""
        chunks = pgvector_store.get_chunks_by_document(doc_id)
        if not chunks:
            logger.warning("cross_doc.skip_no_chunks doc=%s", doc_id[:8])
            return

        doc_entities = self._extractor.extract(chunks)
        pgvector_store.save_doc_entities(doc_id, doc_entities)

        # Doc-level embedding (mean-pooled chunk vectors) for channel-3 fallback.
        try:
            vecs = [c["embedding"] for c in chunks if c.get("embedding") is not None]
            if vecs:
                doc_emb = np.array(vecs, dtype=np.float64).mean(axis=0)
                pgvector_store.upsert_doc_embedding(doc_id, doc_emb.tolist(), len(vecs))
        except Exception:
            logger.exception("cross_doc.doc_embedding_failed doc=%s", doc_id[:8])

        all_ids = pgvector_store.get_all_doc_ids_with_entities()
        other_ids = [did for did in all_ids if did != doc_id]
        if not other_ids:
            return

        others_entities = pgvector_store.get_doc_entities_bulk(other_ids)
        all_entities_map: dict[str, list[tuple[str, int]]] = {doc_id: doc_entities}
        all_entities_map.update(others_entities)
        self._extractor.refresh_global_stats(all_entities_map)

        relations: list[dict] = []
        for other_id, other_entities in others_entities.items():
            if not other_entities:
                continue
            cosine = self._extractor.cosine_between(doc_entities, other_entities)
            if cosine < _COMPLEMENTARY_THRESHOLD:
                continue
            jaccard = self._extractor.jaccard_entities(doc_entities, other_entities)
            rtype = self._extractor.classify_relation(cosine, jaccard)
            # Bidirectional storage so each direction lookup is O(1).
            cosine_scaled = int(cosine * 1000)
            jaccard_scaled = int(jaccard * 1000)
            for src, tgt in ((doc_id, other_id), (other_id, doc_id)):
                relations.append({
                    "source_doc": src,
                    "target_doc": tgt,
                    "cosine": cosine_scaled,
                    "entity_jaccard": jaccard_scaled,
                    "relation_type": rtype,
                })

        pgvector_store.replace_doc_relations(doc_id, relations)
        logger.info(
            "cross_doc.updated doc=%s entities=%d relations=%d",
            doc_id[:8], len(doc_entities), len(relations),
        )

    def rebuild_all(self) -> None:
        """Full pairwise rebuild. O(N^2). Use only when N < 1000.

        Bootstraps from Document table (not doc_entities), extracts entities
        and embeddings for any docs that lack them, then computes relations.
        """
        from app.store.db import get_db_ctx, Document
        with get_db_ctx() as session:
            rows = session.query(Document.document_id).all()
        all_ids = [r[0] for r in rows]
        if len(all_ids) < 2:
            logger.info("cross_doc.rebuild_all skipped (%d docs)", len(all_ids))
            return

        all_entities: dict[str, list[tuple[str, int]]] = {}
        import numpy as np
        for doc_id in all_ids:
            entities = pgvector_store.get_doc_entities_bulk([doc_id]).get(doc_id)
            if not entities:
                chunks = pgvector_store.get_chunks_by_document(doc_id)
                if chunks:
                    entities = self._extractor.extract(chunks)
                    pgvector_store.save_doc_entities(doc_id, entities)
                    vecs = [c["embedding"] for c in chunks if c.get("embedding") is not None]
                    if vecs:
                        doc_emb = np.array(vecs, dtype=np.float64).mean(axis=0)
                        pgvector_store.upsert_doc_embedding(doc_id, doc_emb.tolist(), len(vecs))
            if entities:
                all_entities[doc_id] = entities

        self._extractor.refresh_global_stats(all_entities)
        pgvector_store.clear_all_relations()

        relations: list[dict] = []
        # i<j iteration — already deduplicated by index ordering.
        for i in range(len(all_ids)):
            doc_id = all_ids[i]
            entities = all_entities.get(doc_id, [])
            if not entities:
                continue
            for j in range(i + 1, len(all_ids)):
                other_id = all_ids[j]
                other_entities = all_entities.get(other_id, [])
                if not other_entities:
                    continue
                cosine = self._extractor.cosine_between(entities, other_entities)
                if cosine < _COMPLEMENTARY_THRESHOLD:
                    continue
                jaccard = self._extractor.jaccard_entities(entities, other_entities)
                rtype = self._extractor.classify_relation(cosine, jaccard)
                cosine_scaled = int(cosine * 1000)
                jaccard_scaled = int(jaccard * 1000)
                for src, tgt in ((doc_id, other_id), (other_id, doc_id)):
                    relations.append({
                        "source_doc": src,
                        "target_doc": tgt,
                        "cosine": cosine_scaled,
                        "entity_jaccard": jaccard_scaled,
                        "relation_type": rtype,
                    })

        pgvector_store.bulk_save_relations(relations)
        logger.info(
            "cross_doc.rebuild_all docs=%d relations=%d",
            len(all_ids), len(relations),
        )


# ── Cross-document retriever ─────────────────────────────────────────────

class CrossDocRetriever:
    """Three-channel cross-document jump decision.

    Channel 1: precomputed TF-IDF relation edges (only 'complementary' edges).
    Channel 2: query entity overlap with target doc top-words (low-TF recall).
    Channel 3: doc-level embedding cosine (semantic fallback, N>1000).

    All DB calls are sync (SQLAlchemy). DO NOT add LLM calls without wrapping
    them in asyncio.to_thread — this method runs inside the SSE chat handler.
    """

    def __init__(self) -> None:
        self._extractor = TfidfFeatureExtractor()

    async def retrieve(
        self,
        query: str,
        query_emb: list[float] | None,
        kb_ids: list[str] | None,
        initial_chunks: list[dict],
        user_role_ids: list[int] | None = None,
        can_read_all: bool = False,
    ) -> list[dict]:
        """Return extra chunks from related documents to amplify initial retrieval."""
        if not initial_chunks:
            return []

        matched_doc_ids: set[str] = set()
        for c in initial_chunks:
            did = _chunk_attr(c, "document_id")
            if did:
                matched_doc_ids.add(did)
        if not matched_doc_ids:
            return []

        t0 = time.monotonic()
        seen_ids: set[str] = {_chunk_attr(c, "chunk_id") for c in initial_chunks}

        query_term_set = {t for t, _ in self._extractor.extract_from_text(query)}
        # Single dict used both as set (membership) and as score accumulator.
        neighbor_scores: dict[str, float] = defaultdict(float)

        # Channel 1: doc_relations table (only follow 'complementary' edges).
        for doc_id in matched_doc_ids:
            for rel in pgvector_store.get_doc_relations(doc_id):
                target = rel["target_doc"]
                if target in matched_doc_ids:
                    continue
                if rel["relation_type"] == "complementary":
                    neighbor_scores[target] = max(neighbor_scores[target], rel["cosine_scaled"])

        # Channel 2: query keyword overlap — independently discover docs with
        # matching entity terms, regardless of whether channel 1 found them.
        if query_term_set:
            all_entity_ids = pgvector_store.get_all_doc_ids_with_entities(kb_ids)
            ch2_candidates = [
                d for d in all_entity_ids if d not in matched_doc_ids
            ][:_CH2_MAX_CANDIDATES]
            if ch2_candidates:
                for ndoc_id, entities in pgvector_store.get_doc_entities_bulk(
                    ch2_candidates
                ).items():
                    if not entities:
                        continue
                    entity_set = {e for e, _ in entities}
                    overlap = query_term_set & entity_set
                    if not overlap:
                        continue
                    match_ratio = len(overlap) / max(len(query_term_set), 1)
                    if match_ratio >= _QUERY_KEYWORD_MATCH_RATIO:
                        neighbor_scores[ndoc_id] = max(neighbor_scores[ndoc_id], match_ratio)

        # Channel 3: doc-level embedding cosine (semantic fallback).
        threshold = getattr(settings, "cross_doc_embedding_threshold", 0.7)
        if query_emb is not None:
            for ndoc_id in list(neighbor_scores):
                doc_emb = pgvector_store.get_doc_embedding(ndoc_id)
                if doc_emb is None:
                    continue
                cos_sim = _cosine_similarity(query_emb, doc_emb)
                if cos_sim >= threshold:
                    neighbor_scores[ndoc_id] = max(neighbor_scores[ndoc_id], cos_sim)

        if not neighbor_scores:
            return []

        # Pick top neighbors and fetch their chunks in one bulk query.
        top_neighbors = sorted(neighbor_scores, key=neighbor_scores.get, reverse=True)[
            :_MAX_NEIGHBORS_PER_QUERY
        ]
        if not kb_ids:
            kb_ids = pgvector_store.list_kb_ids()

        bulk_chunks = pgvector_store.get_chunks_by_documents_bulk(
            top_neighbors,
            user_role_ids=user_role_ids,
            can_read_all=can_read_all,
        )
        extra_chunks: list[dict] = []
        for ndoc_id, chunks in bulk_chunks.items():
            for c in chunks:
                cid = _chunk_attr(c, "chunk_id")
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                c["score"] = neighbor_scores.get(ndoc_id, 0.3)
                extra_chunks.append(c)

        logger.info(
            "cross_doc.retrieve query_terms=%d matched_docs=%d neighbors=%d extra=%d elapsed_ms=%.1f",
            len(query_term_set), len(matched_doc_ids), len(neighbor_scores),
            len(extra_chunks), (time.monotonic() - t0) * 1000,
        )
        return extra_chunks


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Vanilla cosine via numpy. Used for channel 3 (4096-dim embedding)."""
    a_arr = np.array(a, dtype=np.float64)
    b_arr = np.array(b, dtype=np.float64)
    norm_a = float(np.linalg.norm(a_arr))
    norm_b = float(np.linalg.norm(b_arr))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


# ── Cross-document synthesizer ───────────────────────────────────────────

class CrossDocSynthesizer:
    """Group chunks by document, annotate source labels for LLM synthesis.

    Returns:
        annotated_texts: one block per document, prefixed with [来源: ...]
        doc_groups: [{document_id, filename, title, chunk_count}]
    """

    def synthesize_texts(self, chunks: list[Any]) -> tuple[list[str], list[dict]]:
        groups: dict[str, dict[str, Any]] = {}
        for c in chunks:
            doc_id = _chunk_attr(c, "document_id")
            if not doc_id:
                continue
            if doc_id not in groups:
                groups[doc_id] = {
                    "filename": _chunk_attr(c, "filename"),
                    "title": _chunk_attr(c, "title"),
                    "chunks": [],
                }
            groups[doc_id]["chunks"].append(c)

        annotated_texts: list[str] = []
        doc_groups: list[dict] = []
        for doc_id, group in groups.items():
            label = group["filename"] or group["title"] or doc_id[:8]
            parts = [f"[来源: {label}]"]
            for c in group["chunks"]:
                text = _chunk_attr(c, "text")
                section = _chunk_attr(c, "section_path")
                if section:
                    parts.append(f"  [{section}] {text}")
                else:
                    parts.append(f"  {text}")
            annotated_texts.append("\n".join(parts))
            doc_groups.append({
                "document_id": doc_id,
                "filename": group["filename"],
                "title": group["title"] or group["filename"] or doc_id[:8],
                "chunk_count": len(group["chunks"]),
            })
        return annotated_texts, doc_groups


# ── Module-level singletons ──────────────────────────────────────────────

cross_doc_builder = DocRelationBuilder()
cross_doc_retriever = CrossDocRetriever()
cross_doc_synthesizer = CrossDocSynthesizer()

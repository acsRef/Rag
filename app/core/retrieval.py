from app.vector.chroma_store import chroma_store
from app.llm.embedding import sf_embedding
from app.llm.rerank import sf_rerank
from app.models.schemas import IntentResult, RetrievedChunk
from app.config import settings


class RetrievalEngine:
    def retrieve(
        self,
        query: str,
        intent: IntentResult,
    ) -> list[RetrievedChunk]:
        top_k = settings.vector_search_top_k
        if not intent.matches:
            return []

        query_emb = sf_embedding.embed(query)
        target_kb_ids = [m.kb_id for m in intent.matches]
        seen_ids = set()
        results: list[dict] = []

        for kb_id in target_kb_ids:
            try:
                chunks = chroma_store.search(kb_id, query_emb, top_k)
                for c in chunks:
                    if c["chunk_id"] not in seen_ids:
                        seen_ids.add(c["chunk_id"])
                        results.append(c)
            except Exception:
                continue

        min_confidence = min(m.score for m in intent.matches) if intent.matches else 0
        if len(results) < top_k and min_confidence < 0.6:
            all_kb_ids = chroma_store.list_collections()
            for kb_id in all_kb_ids:
                if kb_id in target_kb_ids:
                    continue
                try:
                    chunks = chroma_store.search(kb_id, query_emb, top_k)
                    for c in chunks:
                        if c["chunk_id"] not in seen_ids:
                            seen_ids.add(c["chunk_id"])
                            results.append(c)
                except Exception:
                    continue

        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:top_k]

        # Rerank
        if results:
            texts = [r["text"] for r in results]
            try:
                reranked = sf_rerank.rerank(query, texts)
                reranked_ids = [r["index"] for r in reranked]
                results = [results[i] for i in reranked_ids]
            except Exception:
                pass

        return [RetrievedChunk(**r) for r in results]


retrieval_engine = RetrievalEngine()

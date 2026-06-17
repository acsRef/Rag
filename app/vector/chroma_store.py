from app.config import settings
import chromadb
from chromadb.config import Settings as ChromaSettings
from typing import Optional


class ChromaStore:
    def __init__(self):
        self.client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def get_or_create_collection(self, name: str):
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def delete_collection(self, name: str):
        self.client.delete_collection(name)

    def list_collections(self) -> list[str]:
        return [c.name for c in self.client.list_collections()]

    def add_chunks(
        self,
        kb_id: str,
        chunk_ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: Optional[list[dict]] = None,
    ):
        collection = self.get_or_create_collection(kb_id)
        collection.add(
            ids=chunk_ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def search(
        self,
        kb_id: str,
        embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        collection = self.get_or_create_collection(kb_id)
        results = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        chunks = []
        if not results["ids"] or not results["ids"][0]:
            return chunks
        for i, doc_id in enumerate(results["ids"][0]):
            chunks.append({
                "chunk_id": doc_id,
                "text": results["documents"][0][i],
                "score": 1 - (results["distances"][0][i] if results["distances"] else 0),
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            })
        return chunks

    def search_all(
        self,
        kb_ids: list[str],
        embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        all_chunks = []
        for kb_id in kb_ids:
            try:
                chunks = self.search(kb_id, embedding, top_k)
                all_chunks.extend(chunks)
            except Exception:
                continue
        all_chunks.sort(key=lambda x: x["score"], reverse=True)
        return all_chunks[:top_k]

    def delete_document(self, kb_id: str, document_id: str):
        collection = self.get_or_create_collection(kb_id)
        all_records = collection.get(where={"document_id": document_id})
        if all_records["ids"]:
            collection.delete(ids=all_records["ids"])


chroma_store = ChromaStore()

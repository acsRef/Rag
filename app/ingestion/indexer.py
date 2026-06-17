"""Full indexing pipeline: parse → clean → structure → chunk → metadata → embed → store.

Coordinates all ingestion stages and persists results to ChromaDB + SQLite.
"""

from app.vector.chroma_store import chroma_store
from app.llm.embedding import sf_embedding
from app.ingestion.chunker import text_chunker, Chunk
from app.ingestion.cleaner import document_cleaner
from app.ingestion.structurer import document_structurer
from app.ingestion.metadata import chunk_metadata_generator
from app.store.db import DocumentRecord, get_session, new_id
from typing import Optional


class DocumentIndexer:
    """Orchestrates the full document ingestion pipeline."""

    def index(
        self,
        filename: str,
        content: bytes,
        kb_id: str = "default",
        user_id: str = "default_user",
    ) -> dict:
        from app.ingestion.parser import document_parser

        # Step 1: Parse document bytes into Markdown text (type-dispatch via suffix)
        text = document_parser.parse_bytes(content, filename)

        # Step 2: Clean text (normalize line endings, strip control chars, BOM, page markers, etc.)
        text = document_cleaner.clean(text)

        # Step 3: Structure analysis — identify headings, paragraphs, code blocks, tables, images
        sections = document_structurer.structure(text)

        # Step 4: Smart chunking — structure-aware, atomic-block-protected, with overlap
        chunks: list[Chunk] = text_chunker.chunk(sections)

        if not chunks:
            doc_id = new_id()
            self._save_document_record(doc_id, user_id, kb_id, filename, 0, "failed")
            return {"document_id": doc_id, "chunk_count": 0, "status": "failed"}

        # Step 5: Batch-generate title / summary / questions via MiniMax LLM
        chunks = chunk_metadata_generator.generate(chunks)

        # Step 6: Generate embeddings for all chunks in one batch
        embeddings = sf_embedding.embed_batch([c.text for c in chunks])

        # Step 7: Prepare metadata and write to ChromaDB
        doc_id = new_id()
        chunk_ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "document_id": doc_id,
                "filename": filename,
                "chunk_index": i,
                "kb_id": kb_id,
                "title": c.title,
                "summary": c.summary,
                "questions": "; ".join(c.questions),
                "section_path": " > ".join(c.section_path) if c.section_path else "",
            }
            for i, c in enumerate(chunks)
        ]

        chroma_store.add_chunks(
            kb_id=kb_id,
            chunk_ids=chunk_ids,
            texts=[c.text for c in chunks],
            embeddings=embeddings,
            metadatas=metadatas,
        )

        # Step 8: Persist document record to SQLite
        self._save_document_record(doc_id, user_id, kb_id, filename, len(chunks), "indexed")

        return {
            "document_id": doc_id,
            "filename": filename,
            "status": "indexed",
            "chunk_count": len(chunks),
        }

    def _save_document_record(
        self, doc_id: str, user_id: str, kb_id: str,
        filename: str, chunk_count: int, status: str,
    ):
        """Insert a DocumentRecord row into SQLite."""
        session = get_session()
        try:
            record = DocumentRecord(
                document_id=doc_id,
                user_id=user_id,
                kb_id=kb_id,
                filename=filename,
                chunk_count=chunk_count,
                status=status,
            )
            session.add(record)
            session.commit()
        finally:
            session.close()


document_indexer = DocumentIndexer()

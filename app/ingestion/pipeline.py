from app.ingestion.indexer import document_indexer
from typing import Optional


class IngestionPipeline:
    async def run(
        self,
        filename: str,
        content: bytes,
        kb_id: str = "default",
        user_id: str = "default_user",
    ) -> dict:
        return document_indexer.index(
            filename=filename,
            content=content,
            kb_id=kb_id,
            user_id=user_id,
        )


ingestion_pipeline = IngestionPipeline()

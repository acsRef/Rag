import logging

from app.config import settings
from openai import OpenAI

logger = logging.getLogger(__name__)


class SFEmbedding:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            timeout=60.0,
            max_retries=2,
        )
        self.model = settings.embedding_model

    def embed(self, text: str) -> list[float]:
        try:
            resp = self.client.embeddings.create(model=self.model, input=text)
            return resp.data[0].embedding
        except Exception as e:
            logger.exception("Embedding API failed for single text")
            raise RuntimeError("向量服务暂不可用，请稍后重试") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self.client.embeddings.create(model=self.model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:
            logger.exception("Embedding API failed for batch of %d texts", len(texts))
            raise RuntimeError("向量服务暂不可用，请稍后重试") from e


sf_embedding = SFEmbedding()

from app.config import settings
from openai import OpenAI


class SFEmbedding:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
        )
        self.model = settings.embedding_model

    def embed(self, text: str) -> list[float]:
        resp = self.client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


sf_embedding = SFEmbedding()

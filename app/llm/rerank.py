from app.config import settings
import httpx


class SFRerank:
    def __init__(self):
        self.api_key = settings.siliconflow_api_key
        self.base_url = settings.siliconflow_base_url
        self.model = settings.rerank_model

    def rerank(self, query: str, documents: list[str]) -> list[dict]:
        url = f"{self.base_url}/rerank"
        raw = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "query": query, "documents": documents},
            timeout=30,
        )
        data = raw.json()
        results = data.get("results", [])
        results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return results


sf_rerank = SFRerank()

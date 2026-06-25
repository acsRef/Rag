"""SiliconFlow reranker client with circuit breaker and retry."""

import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.llm.base import CircuitOpenError, jittered_backoff, provider_health

logger = logging.getLogger(__name__)


class SFRerank:
    provider = "siliconflow"

    def __init__(self):
        self.api_key = settings.siliconflow_api_key
        self.base_url = settings.siliconflow_base_url
        self.model = settings.rerank_model

    def _check_breaker(self) -> None:
        if not settings.circuit_breaker_enabled:
            return
        breaker = provider_health.get(self.provider)
        if not breaker.allow_request():
            raise CircuitOpenError("SiliconFlow rerank circuit breaker is open")

    def _on_success(self) -> None:
        if settings.circuit_breaker_enabled:
            provider_health.get(self.provider).on_success()

    def _on_failure(self) -> None:
        if settings.circuit_breaker_enabled:
            provider_health.get(self.provider).on_failure()

    def rerank(
        self,
        query: str,
        documents: list[str],
        max_retries: int = 2,
    ) -> list[dict[str, Any]]:
        """Rerank documents with retry + circuit breaker."""
        for attempt in range(max_retries + 1):
            self._check_breaker()
            try:
                url = f"{self.base_url}/rerank"
                resp = httpx.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "query": query, "documents": documents},
                    timeout=30,
                )
                # Treat 4xx as non-retryable failures
                if resp.status_code >= 400:
                    if 400 <= resp.status_code < 500:
                        self._on_failure()
                        logger.error("Rerank 4xx: %d %s", resp.status_code, resp.text[:200])
                        return []
                    # 5xx — retryable
                    self._on_failure()
                    if attempt < max_retries:
                        time.sleep(jittered_backoff(attempt))
                        continue
                    logger.error("Rerank 5xx after retries: %d", resp.status_code)
                    return []

                self._on_success()
                data = resp.json()
                results = data.get("results", [])
                results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
                return results

            except CircuitOpenError:
                raise
            except httpx.TimeoutException:
                self._on_failure()
                if attempt < max_retries:
                    time.sleep(jittered_backoff(attempt))
                    continue
                logger.exception("Rerank timeout after retries")
                return []
            except Exception:
                self._on_failure()
                if attempt < max_retries:
                    time.sleep(jittered_backoff(attempt))
                    continue
                logger.exception("Rerank failed for query=%s", query[:50])
                return []


sf_rerank = SFRerank()

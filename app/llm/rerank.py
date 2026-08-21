"""SiliconFlow reranker client — async, with circuit breaker and retry."""

import asyncio
import logging
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
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int = -1  # 对齐 embedding/chat 的 loop 重建模式

    def _get_client(self) -> httpx.AsyncClient:
        # httpx.AsyncClient 绑死在创建它的事件循环上；跨 loop 调用会报
        # "Event loop is closed"。ingestion 用 asyncio.run 派生工作线程、
        # 测试场景下 loop 变化时强制重建。
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = -1
        if self._client is None or self._client_loop_id != current_loop_id:
            self._client = httpx.AsyncClient(timeout=30)
            self._client_loop_id = current_loop_id
        return self._client

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

    async def rerank(
        self,
        query: str,
        documents: list[str],
        max_retries: int = 2,
    ) -> list[dict[str, Any]]:
        client = self._get_client()
        for attempt in range(max_retries + 1):
            self._check_breaker()
            try:
                resp = await client.post(
                    f"{self.base_url}/rerank",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "query": query, "documents": documents},
                )
                if resp.status_code == 429:
                    # Rate limit — retryable, don't count as circuit failure
                    if attempt < max_retries:
                        await asyncio.sleep(jittered_backoff(attempt))
                        continue
                    logger.error("Rerank 429 after retries")
                    return []
                if 400 <= resp.status_code < 500:
                    # Client error — NOT a provider failure, skip breaker
                    logger.error("Rerank 4xx: %d %s", resp.status_code, resp.text[:200])
                    return []
                if resp.status_code >= 500:
                    # 5xx — retryable
                    self._on_failure()
                    if attempt < max_retries:
                        await asyncio.sleep(jittered_backoff(attempt))
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
                    await asyncio.sleep(jittered_backoff(attempt))
                    continue
                logger.exception("Rerank timeout after retries")
                return []
            except Exception:
                self._on_failure()
                if attempt < max_retries:
                    await asyncio.sleep(jittered_backoff(attempt))
                    continue
                logger.exception("Rerank failed for query=%s", query[:50])
                return []
        return []


sf_rerank = SFRerank()

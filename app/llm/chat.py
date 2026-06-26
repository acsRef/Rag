"""MiniMax M3 chat client with circuit breaker — async."""

import asyncio
import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.config import settings
from app.llm.base import CircuitOpenError, jittered_backoff, provider_health

logger = logging.getLogger(__name__)


class MiniMaxClient:
    provider = "minimax"

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
        )
        self.model = settings.minimax_model

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def _check_breaker(self) -> None:
        if not settings.circuit_breaker_enabled:
            return
        breaker = provider_health.get(self.provider)
        if not breaker.allow_request():
            raise CircuitOpenError("MiniMax circuit breaker is open")

    def _on_success(self) -> None:
        if settings.circuit_breaker_enabled:
            provider_health.get(self.provider).on_success()

    def _on_failure(self) -> None:
        if settings.circuit_breaker_enabled:
            provider_health.get(self.provider).on_failure()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat — async generator."""
        self._check_breaker()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                temperature=temperature,
                top_p=top_p,
                max_tokens=4096,
            )
            first_token = True
            async for chunk in response:
                if first_token:
                    self._on_success()
                    first_token = False
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        except CircuitOpenError:
            raise
        except Exception:
            self._on_failure()
            raise

    async def chat(
        self,
        messages: list[dict],
        timeout: int = 120,
        max_retries: int = 2,
        max_tokens: int | None = None,
    ) -> str:
        """Sync-style chat — async under the hood."""
        for attempt in range(max_retries + 1):
            self._check_breaker()
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=False,
                    temperature=0.7,
                    max_tokens=max_tokens if max_tokens is not None else 4096,
                    timeout=timeout,
                )
                if not response.choices:
                    self._on_success()
                    return ""
                self._on_success()
                return response.choices[0].message.content or ""
            except CircuitOpenError:
                raise
            except Exception as e:
                self._on_failure()
                if attempt < max_retries:
                    await asyncio.sleep(jittered_backoff(attempt))
                    continue
                raise RuntimeError(f"MiniMax chat 调用失败: {e}") from e


minimax_client = MiniMaxClient()

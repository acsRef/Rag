"""MiniMax M3 chat client with circuit breaker — async."""

import asyncio
import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.config import settings
from app.llm.base import CircuitOpenError, PermanentError, RateLimitError, classify_llm_error, provider_health

logger = logging.getLogger(__name__)


class MiniMaxClient:
    provider = "minimax"

    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._client_loop_id: int | None = None
        self.model = settings.minimax_model

    @property
    def client(self) -> AsyncOpenAI:
        try:
            current_loop = asyncio.get_running_loop()
            current_id = id(current_loop)
        except RuntimeError:
            current_loop = None
            current_id = -1

        if self._client is None or self._client_loop_id != current_id:
            self._client = AsyncOpenAI(
                api_key=settings.minimax_api_key,
                base_url=settings.minimax_base_url,
                timeout=90.0,
            )
            self._client_loop_id = current_id
        return self._client

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
                model=self.model,  # 流式只用于文本对话；vision 走 chat(model=...)
                messages=messages,
                stream=True,
                temperature=temperature,
                top_p=top_p,
                max_tokens=4096,
            )
            first_token = True
            async with response:
                async for chunk in response:
                    if first_token:
                        self._on_success()
                        first_token = False
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
        except CircuitOpenError:
            raise
        except Exception as e:
            typed, _ = classify_llm_error(e)
            # 429/4xx 永久错误均不计熔断失败（AGENTS §8）
            if not isinstance(typed, (PermanentError, RateLimitError)):
                self._on_failure()
            raise typed

    async def chat(
        self,
        messages: list[dict],
        timeout: int = 120,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """Single-attempt chat — 重试策略统一由 call_llm_with_retry 负责。

        本方法只做：单次调用 + 熔断记账 + 错误分类抛出。
        `model` 覆盖默认模型：图片理解必须用多模态模型（settings.vision_model），
        即使文本对话模型切成了非多模态的 highspeed 变体。
        （旧版自带重试循环，与 call_llm_with_retry 叠加会放大到 9 次。）
        """
        self._check_breaker()
        try:
            response = await self.client.chat.completions.create(
                model=model or self.model,  # vision 调用经 model= 固定走多模态模型
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
            typed, _ = classify_llm_error(e)
            # 429/4xx 永久错误均不计熔断失败
            if not isinstance(typed, (PermanentError, RateLimitError)):
                self._on_failure()
            raise typed


minimax_client = MiniMaxClient()

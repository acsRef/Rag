"""SiliconFlow embedding client — async, with rate limiter, retry, fallback, and circuit breaker."""

import asyncio
import logging
import random
import time

from openai import AsyncOpenAI, RateLimitError, APIStatusError

from app.config import settings
from app.llm.base import CircuitOpenError, PermanentError, classify_llm_error, jittered_backoff, provider_health

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter — async, with lock."""

    def __init__(self, rps: int):
        self.rps = rps
        self.tokens = float(rps)
        self.last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(float(self.rps), self.tokens + elapsed * self.rps)
            if self.tokens < 1:
                sleep_for = (1 - self.tokens) / self.rps
                await asyncio.sleep(sleep_for)
                self.tokens = 0
            else:
                self.tokens -= 1


# Kept as a module-level import to avoid circular import on first eval
import time as _time


class SFEmbedding:
    provider = "siliconflow"

    def __init__(self):
        self.model = settings.embedding_model
        self.limiter = RateLimiter(settings.embedding_rate_limit_rps)
        self._client: AsyncOpenAI | None = None
        self._client_loop_id: int | None = None

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy AsyncOpenAI client that recreates if the event loop changed.

        httpx.AsyncClient is tied to the event loop that created it. If we
        call embed from a synchronous context (e.g. indexer via asyncio.run),
        a new loop is created and then closed, corrupting the client for the
        main async loop. Recreating on loop change avoids this entirely.
        """
        try:
            current_loop = asyncio.get_running_loop()
            current_id = id(current_loop)
        except RuntimeError:
            current_loop = None
            current_id = -1

        if self._client is None or self._client_loop_id != current_id:
            self._client = AsyncOpenAI(
                api_key=settings.siliconflow_api_key,
                base_url=settings.siliconflow_base_url,
                timeout=15.0,
                max_retries=0,
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
            raise CircuitOpenError("SiliconFlow embedding circuit breaker is open")

    def _on_success(self) -> None:
        if settings.circuit_breaker_enabled:
            provider_health.get(self.provider).on_success()

    def _on_failure(self) -> None:
        if settings.circuit_breaker_enabled:
            provider_health.get(self.provider).on_failure()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed(self, text: str, max_retries: int = 1) -> list[float]:
        for attempt in range(max_retries + 1):
            self._check_breaker()
            await self.limiter.acquire()
            try:
                resp = await self.client.embeddings.create(model=self.model, input=text)
                self._on_success()
                return resp.data[0].embedding
            except CircuitOpenError:
                raise
            except Exception as e:
                typed, should_retry = classify_llm_error(e)
                if not isinstance(typed, PermanentError):
                    self._on_failure()
                if should_retry and attempt < max_retries:
                    await asyncio.sleep(jittered_backoff(attempt))
                    continue
                logger.exception("Embedding API failed for single text: %s", typed)
                raise typed

    async def embed_single_chunk(self, text: str, attempt: int = 0) -> tuple[list[float] | None, str | None]:
        self._check_breaker()
        await self.limiter.acquire()
        try:
            resp = await self.client.embeddings.create(model=self.model, input=text)
            self._on_success()
            return (resp.data[0].embedding, None)
        except CircuitOpenError:
            raise
        except RateLimitError as e:
            retry_after = _parse_retry_after(e) or (settings.embedding_backoff_base * (2 ** attempt))
            if attempt < settings.embedding_max_retries:
                await _jittered_sleep(retry_after)
                return await self.embed_single_chunk(text, attempt + 1)
            self._on_failure()
            return (None, f"请求限流（429），已重试{settings.embedding_max_retries}次")
        except APIStatusError as e:
            if e.status_code >= 500 and attempt < settings.embedding_max_retries:
                backoff = settings.embedding_backoff_base * (2 ** attempt)
                await _jittered_sleep(backoff)
                return await self.embed_single_chunk(text, attempt + 1)
            self._on_failure()
            return (None, f"API 错误 ({e.status_code}): {e.message}")
        except Exception as e:
            self._on_failure()
            logger.warning("Embedding chunk failed: %s", e)
            return (None, str(e))

    async def embed_with_fallback(self, texts: list[str]) -> list[tuple[list[float] | None, str | None]]:
        if not texts:
            return []

        t0 = _time.monotonic()
        logger.debug("embed.batch.start batch=%d", len(texts))
        batch_result = await _try_batch_with_retry(self, texts)
        if batch_result is not None:
            logger.info("embed.batch.ok batch=%d elapsed_ms=%.1f", len(texts), (_time.monotonic() - t0) * 1000)
            return [(emb, None) for emb in batch_result]

        logger.warning("Batch embedding failed for %d texts, falling back to single-chunk", len(texts))
        results = []
        ok_count = 0
        for i, text in enumerate(texts):
            t_chunk = _time.monotonic()
            emb, err = await self.embed_single_chunk(text)
            chunk_ms = (_time.monotonic() - t_chunk) * 1000
            if emb is not None:
                ok_count += 1
                logger.debug("embed.single.ok idx=%d/%d elapsed_ms=%.1f", i + 1, len(texts), chunk_ms)
            else:
                logger.warning("embed.single.fail idx=%d/%d elapsed_ms=%.1f err=%s", i + 1, len(texts), chunk_ms, err)
            results.append((emb, err))
        logger.info("embed.fallback.done batch=%d ok=%d fail=%d elapsed_ms=%.1f",
                     len(texts), ok_count, len(texts) - ok_count, (_time.monotonic() - t0) * 1000)
        return results


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

async def _try_batch_with_retry(sf: SFEmbedding, texts: list[str], attempt: int = 0) -> list[list[float]] | None:
    sf._check_breaker()
    await sf.limiter.acquire()
    try:
        resp = await sf.client.embeddings.create(model=sf.model, input=texts)
        sf._on_success()
        return [d.embedding for d in resp.data]
    except CircuitOpenError:
        raise
    except RateLimitError as e:
        retry_after = _parse_retry_after(e) or (settings.embedding_backoff_base * (2 ** attempt))
        if attempt < settings.embedding_max_retries:
            await _jittered_sleep(retry_after)
            return await _try_batch_with_retry(sf, texts, attempt + 1)
        sf._on_failure()
        return None
    except APIStatusError as e:
        if e.status_code >= 500 and attempt < settings.embedding_max_retries:
            backoff = settings.embedding_backoff_base * (2 ** attempt)
            await _jittered_sleep(backoff)
            return await _try_batch_with_retry(sf, texts, attempt + 1)
        sf._on_failure()
        return None
    except Exception as e:
        logger.debug("embed.batch.exception attempt=%d type=%s msg=%s", attempt, type(e).__name__, str(e)[:200])
        sf._on_failure()
        return None


def _parse_retry_after(e: RateLimitError) -> float | None:
    try:
        val = e.response.headers.get("retry-after")
        if val:
            return float(val)
    except Exception:
        pass
    return None


async def _jittered_sleep(seconds: float):
    jitter = random.uniform(0.8, 1.2)
    await asyncio.sleep(seconds * jitter)


sf_embedding = SFEmbedding()

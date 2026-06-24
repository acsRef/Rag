import logging
import time
import random

from app.config import settings
from openai import OpenAI
from openai import RateLimitError, APIStatusError

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter."""

    def __init__(self, rps: int):
        self.rps = rps
        self.tokens = float(rps)
        self.last = time.monotonic()

    def acquire(self):
        now = time.monotonic()
        elapsed = now - self.last
        self.last = now
        self.tokens = min(float(self.rps), self.tokens + elapsed * self.rps)
        if self.tokens < 1:
            sleep_for = (1 - self.tokens) / self.rps
            time.sleep(sleep_for)
            self.tokens = 0
        else:
            self.tokens -= 1


class SFEmbedding:
    def __init__(self):
        # timeout 提到 90s:PDF 解析出来的 chunk 可能含奇怪 token 触发服务端慢路径
        # (例:政府工作报告 chunk 0 包含 `<!-- image -->` + 全角数字的 mix,曾 60s 超时)
        self.client = OpenAI(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            timeout=90.0,
            max_retries=0,
        )
        self.model = settings.embedding_model
        self.limiter = RateLimiter(settings.embedding_rate_limit_rps)

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

    def embed_single_chunk(self, text: str, attempt: int = 0) -> tuple[list[float] | None, str | None]:
        """Try to embed a single chunk text. Returns (embedding, error_message)."""
        self.limiter.acquire()
        try:
            resp = self.client.embeddings.create(model=self.model, input=text)
            return (resp.data[0].embedding, None)
        except RateLimitError as e:
            retry_after = _parse_retry_after(e) or (settings.embedding_backoff_base * (2 ** attempt))
            if attempt < settings.embedding_max_retries:
                _jittered_sleep(retry_after)
                return self.embed_single_chunk(text, attempt + 1)
            return (None, f"请求限流（429），已重试{settings.embedding_max_retries}次")
        except APIStatusError as e:
            if e.status_code >= 500 and attempt < settings.embedding_max_retries:
                backoff = settings.embedding_backoff_base * (2 ** attempt)
                _jittered_sleep(backoff)
                return self.embed_single_chunk(text, attempt + 1)
            return (None, f"API 错误 ({e.status_code}): {e.message}")
        except Exception as e:
            logger.warning("Embedding chunk failed: %s", e)
            return (None, str(e))

    def embed_with_fallback(self, texts: list[str]) -> list[tuple[list[float] | None, str | None]]:
        """Try batch embed; fall back to per-chunk on failure.
        Returns list of (embedding_or_None, error_or_None).
        """
        if not texts:
            return []

        t0 = time.monotonic()
        logger.debug("embed.batch.start batch=%d", len(texts))
        batch_result = _try_batch_with_retry(self, texts)
        if batch_result is not None:
            logger.info(
                "embed.batch.ok batch=%d elapsed_ms=%.1f",
                len(texts), (time.monotonic() - t0) * 1000,
            )
            return [(emb, None) for emb in batch_result]

        logger.warning("Batch embedding failed for %d texts, falling back to single-chunk", len(texts))
        results: list[tuple[list[float] | None, str | None]] = []
        ok_count = 0
        for i, text in enumerate(texts):
            t_chunk = time.monotonic()
            emb, err = self.embed_single_chunk(text)
            chunk_ms = (time.monotonic() - t_chunk) * 1000
            if emb is not None:
                ok_count += 1
                logger.debug("embed.single.ok idx=%d/%d elapsed_ms=%.1f", i + 1, len(texts), chunk_ms)
            else:
                logger.warning("embed.single.fail idx=%d/%d elapsed_ms=%.1f err=%s", i + 1, len(texts), chunk_ms, err)
            results.append((emb, err))
        logger.info(
            "embed.fallback.done batch=%d ok=%d fail=%d elapsed_ms=%.1f",
            len(texts), ok_count, len(texts) - ok_count,
            (time.monotonic() - t0) * 1000,
        )
        return results


def _try_batch_with_retry(sf: SFEmbedding, texts: list[str], attempt: int = 0) -> list[list[float]] | None:
    """Try batch embedding with limited retries on transient errors."""
    sf.limiter.acquire()
    try:
        resp = sf.client.embeddings.create(model=sf.model, input=texts)
        return [d.embedding for d in resp.data]
    except RateLimitError as e:
        retry_after = _parse_retry_after(e) or (settings.embedding_backoff_base * (2 ** attempt))
        if attempt < settings.embedding_max_retries:
            _jittered_sleep(retry_after)
            return _try_batch_with_retry(sf, texts, attempt + 1)
        return None
    except APIStatusError as e:
        if e.status_code >= 500 and attempt < settings.embedding_max_retries:
            backoff = settings.embedding_backoff_base * (2 ** attempt)
            _jittered_sleep(backoff)
            return _try_batch_with_retry(sf, texts, attempt + 1)
        return None
    except Exception as e:
        # 包含 timeout / 网络错误 — DEBUG 详细原因,便于排查 chunk 0 这种 timeout
        logger.debug("embed.batch.exception attempt=%d type=%s msg=%s", attempt, type(e).__name__, str(e)[:200])
        return None


def _parse_retry_after(e: RateLimitError) -> float | None:
    try:
        val = e.response.headers.get("retry-after")
        if val:
            return float(val)
    except Exception:
        pass
    return None


def _jittered_sleep(seconds: float):
    jitter = random.uniform(0.8, 1.2)
    time.sleep(seconds * jitter)


sf_embedding = SFEmbedding()

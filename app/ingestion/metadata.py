"""LLM-based metadata generation for each chunk.

Uses MiniMax API calls to generate title / summary / questions, then writes
results back into the Chunk objects. Chunks are processed in fixed-size
batches (one LLM call per batch) to bound the blast radius of any single
failure; a batch that keeps failing degrades to empty metadata instead of
aborting the document or dropping chunks.

生产事故背景（2026-08-24 修复）：
  1. 整文档单次调用：2023 年报一次超时把 436 块的问题全部清零
     （更早一轮同类失败曾静默丢掉 41 个块）。
  2. 截断 JSON：2024/2025 文档返回可解析但只覆盖 12/50 块的响应，
     全库问题覆盖率 ~1%，question channel 形同虚设。
"""

import asyncio
import logging
import time

from app.ingestion.chunker import Chunk
from app.llm.base import PermanentError, call_llm_with_retry, jittered_backoff, robust_json_parse
from app.llm.chat import minimax_client

logger = logging.getLogger(__name__)

# 每批 LLM 调用的块数上限。旧实现单次调用覆盖整文档（~474 块）：响应 JSON 过长
# 易截断（事故根因之二）、一次超时波及全文档（事故根因之一）。分批后单批爆炸
# 半径有界，失败只影响本批。
_BATCH_SIZE = 25
# 批级重试：首试 + 2 次重试 = 共 3 次尝试。临时故障（超时/限流/空响应/
# JSON 解析失败）可重试；永久错误（4xx/鉴权）不重试直接降级本批。
_BATCH_MAX_RETRIES = 2


_FMT = """你是一个企业知识库元数据生成器。
为每个文本块生成 title（10-20字精确标题）、summary（2-3句话，保留数字/日期/条件）、questions（4-5个具体业务问题）。

只输出 JSON，格式：
{{"chunks":[{{"index":0,"title":"...","summary":"...","questions":["?","?","?","?"]}}, ...]}}

--- 示例1：技术参数块 ---
【0】
路径：产品规格 / 技术参数
内容：M3 工业网关工作温度范围-40℃~85℃，支持 Modbus RTU/TCP、OPC UA、S7、MC 等 20+ 工业协议，配备 2 个千兆网口和 4G 模块。
→ {{"chunks":[{{"index":0,"title":"M3工作温度与协议支持","summary":"M3网关可在-40℃~85℃宽温下工作，南向支持Modbus、OPC UA、西门子S7等20余种工业协议，北向支持MQTT/HTTP，配备双千兆网口和4G无线模块。","questions":["M3网关的工作温度范围是多少？","M3支持哪些工业协议？","M3网关的网络接口配置如何？","M3是否支持4G无线通信？"]}}]}}

--- 示例2：财务数据块 ---
【0】
路径：财务分析 / Q4营收
内容：Q4华东战区营收1.2亿（完成率112%），华南0.98亿（91%），华北0.75亿（83%），西南0.42亿（105%）。合计3.35亿。
→ {{"chunks":[{{"index":0,"title":"Q4各战区营收与完成率","summary":"Q4四大战区合计营收3.35亿元。华东完成率最高（112%），西南次之（105%），华北最低（83%）。各战区完成率差异显著，华东和西南超额完成目标。","questions":["Q4营收最高的战区是哪个？完成率多少？","华东战区Q4营收目标完成率是多少？","Q4四大战区合计营收多少？","哪个战区Q4完成率最低？","西南战区Q4营收完成情况如何？"]}}]}}

路径指示文档位置，内容为实际文本。index 为本批内的序号，必须逐条回显且不得遗漏。
{chunks_text}"""

METADATA_PROMPT = _FMT


def _backoff_sleep(attempt: int) -> None:
    """批级重试退避——复用 base.jittered_backoff 的指数+抖动习惯用法。

    同步版（generate 在 ingestion 线程池里跑，无事件循环可 await）。
    独立函数便于测试短路（monkeypatch 后零真实等待）。
    """
    time.sleep(jittered_backoff(attempt))


class ChunkMetadataGenerator:
    """Calls MiniMax once per batch of chunks to generate title/summary/questions."""

    def generate(self, chunks: list[Chunk]) -> list[Chunk]:
        """Fill title/summary/questions for all chunks, batch by batch.

        绝不上抛、绝不丢块：任何一批在全部尝试后仍失败，只记录
        ``ingest.metadata_batch_failed`` 并让该批保持空元数据继续走流程。
        """
        if not chunks:
            return chunks
        for batch_no, start in enumerate(range(0, len(chunks), _BATCH_SIZE)):
            batch = chunks[start : start + _BATCH_SIZE]
            self._generate_batch(batch, batch_no)
        return chunks

    def _generate_batch(self, batch: list[Chunk], batch_no: int) -> None:
        """Run one batch: up to 1 + _BATCH_MAX_RETRIES attempts, then degrade in place."""
        last_err = ""
        attempt = 0
        for attempt in range(_BATCH_MAX_RETRIES + 1):
            try:
                resp = self._call_llm_once(batch)
                # 解析+回填也在保护区：robust_json_parse 遇病态深嵌套可抛
                # RecursionError，旧结构里它在 else 分支逃过了 except——
                # 会击穿 generate() 的「绝不上抛」保证。
                hit = self._apply_response(resp, batch)
            except PermanentError as e:
                # 4xx/鉴权类故障重试无意义——不烧完尝试次数，立即降级本批
                last_err = f"permanent: {e}"
                break
            except Exception as e:  # TemporaryError / CircuitOpen / 未知名——均可重试
                last_err = f"{type(e).__name__}: {e}"
            else:
                if hit:
                    logger.info(
                        "ingest.metadata_batch_ok batch=%d chunks=%d hit=%d",
                        batch_no,
                        len(batch),
                        hit,
                    )
                    return
                # 可接受的部分覆盖算命中；连一条有效条目都没有视为本次失败
                last_err = "unparsable-or-empty-response"
            if attempt < _BATCH_MAX_RETRIES:
                _backoff_sleep(attempt)
        logger.warning(
            "ingest.metadata_batch_failed batch=%d chunks=%d attempt=%d err=%s",
            batch_no,
            len(batch),
            attempt + 1,
            last_err,
        )

    def _call_llm_once(self, batch: list[Chunk]) -> str:
        """Single LLM call for one batch — the seam unit tests patch around."""
        chunks_text = "\n\n".join(
            f"【{i}】\n路径：{' / '.join(c.section_path) if c.section_path else '无'}\n内容：{c.text[:300]}"
            for i, c in enumerate(batch)
        )
        prompt = METADATA_PROMPT.format(chunks_text=chunks_text)
        ntoks = max(1024, len(batch) * 256)
        # 内层 max_retries=0：重试统一由批级循环负责——双层重试会把
        # 「每批共 3 次尝试」放大成 6 次。经统一策略层做类型感知分类
        # （Permanent/Temporary/CircuitOpen 语义不变）。
        resp = asyncio.run(
            call_llm_with_retry(
                minimax_client.chat,
                [{"role": "user", "content": prompt}],
                tag="metadata",
                max_retries=0,
                max_tokens=ntoks,
                timeout=min(120, 15 * len(batch)),
            )
        )
        return resp or ""

    @staticmethod
    def _apply_response(resp: str, batch: list[Chunk]) -> int:
        """Parse ``resp`` and populate matched chunks in place. Returns hit count.

        显式键映射契约：每条目必须回显批内序号 ``index``，解析严格按回显值
        匹配到对应 chunk；缺失 / 非法（非整数）/ 越界的条目一律视为「未命中」，
        对应 chunk 保持空元数据——绝不按列表位置对位。
        部分覆盖（≥1 条命中并填充）是可接受结果，返回命中数由调用方决定成败。
        """
        if not resp or not resp.strip():
            return 0
        data = robust_json_parse(resp)
        if not isinstance(data, dict):
            return 0
        items = data.get("chunks")
        if not isinstance(items, list):
            return 0
        hit = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            # bool 是 int 子类——True/False 作序号属畸形回显，按未命中处理
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if not 0 <= idx < len(batch):
                continue
            filled = False
            new_title = item.get("title")
            if new_title:
                batch[idx].title = new_title
                filled = True
            new_summary = item.get("summary")
            if new_summary:
                batch[idx].summary = new_summary
                filled = True
            new_questions = item.get("questions")
            if new_questions:
                batch[idx].questions = new_questions
                filled = True
            if filled:
                hit += 1
        return hit


chunk_metadata_generator = ChunkMetadataGenerator()

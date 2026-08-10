"""一次性迁移工具：把所有含 markdown 表格的 chunk 文本转成自然语言并重新嵌入。

设计审查 P2-14：从 app/store/pgvector_store.py 外移至此（工具不属于 store 层）。
用法：
    D:/miniConda/envs/rag/python.exe -m tools.clean_all_table_chunks [batch_size]

对每个含 `|` 的 chunk 文本调用 _clean_table_text 清洗；文本变则重新嵌入并更新
text/embedding/search_text。返回更新的 chunk 数。
"""
import asyncio
import logging

from sqlalchemy import text

from app.store.db import get_session
from app.store.pgvector_store import tokenize
from app.ingestion.chunker import _clean_table_text as _table_cleaner

logger = logging.getLogger(__name__)


def _clean_tables_in_text(text: str) -> str:
    """Find markdown table blocks within chunk text and convert to natural language.

    Handles chunk text with section path prefix like:
        【产品规格书 / 2.3 指示灯说明】
        ### 2.3 指示灯说明
        | 指示灯 | 颜色 | 状态含义 |
        |--------|------|---------|
        | PWR | 绿色常亮 | 设备供电正常 |
    """
    lines = text.split("\n")
    result: list[str] = []
    in_table = False
    table_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(stripped)
        else:
            if in_table:
                in_table = False
                cleaned = _table_cleaner("\n".join(table_lines))
                result.append(cleaned)
            result.append(line)
    if in_table:
        cleaned = _table_cleaner("\n".join(table_lines))
        result.append(cleaned)
    return "\n".join(result)


def clean_all_table_chunks(batch_size: int = 20) -> int:
    """Re-process all existing chunks that contain markdown tables.

    Applies _clean_table_text to chunk text, re-embeds cleaned text,
    and updates text/embedding/search_text in the database.

    Returns the number of chunks updated.
    """
    _SQL = text("SELECT chunk_id, document_id, text, embedding FROM chunks WHERE text LIKE '%|%'")
    session = get_session()
    try:
        rows = session.execute(_SQL).fetchall()
    finally:
        session.close()

    if not rows:
        return 0

    logger.info("table_clean.start found=%d", len(rows))
    from app.llm.embedding import sf_embedding

    update_count = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        clean_texts: list[str] = []
        chunk_ids: list[str] = []

        for r in batch:
            chunk_id, doc_id, raw, emb = r
            cleaned = _clean_tables_in_text(raw)
            if cleaned != raw:
                clean_texts.append(cleaned)
                chunk_ids.append(chunk_id)

        if not clean_texts:
            continue

        try:
            emb_results = asyncio.run(sf_embedding.embed_with_fallback(clean_texts))
        except Exception:
            logger.exception("table_clean.embed_failed batch=%d", i // batch_size)
            continue

        new_session = get_session()
        try:
            for cid, cleaned, (new_emb, err) in zip(chunk_ids, clean_texts, emb_results):
                if new_emb is None:
                    logger.warning("table_clean.skip_embed_failed chunk=%s err=%s", cid[:12], err)
                    continue
                new_search = tokenize(cleaned)
                new_session.execute(
                    text("UPDATE chunks SET text = :txt, embedding = :emb, search_text = :st "
                         "WHERE chunk_id = :cid"),
                    {"txt": cleaned, "emb": new_emb, "st": new_search, "cid": cid},
                )
                update_count += 1
            new_session.commit()
        except Exception:
            new_session.rollback()
            logger.exception("table_clean.update_failed batch=%d", i // batch_size)
        finally:
            new_session.close()

        logger.info("table_clean.batch_done batch=%d updated=%d", i // batch_size, update_count)

    logger.info("table_clean.done total_updated=%d", update_count)
    return update_count


if __name__ == "__main__":
    import sys
    from app.core.logging import setup_logging
    setup_logging()
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    n = clean_all_table_chunks(batch)
    print(f"updated {n} chunks")
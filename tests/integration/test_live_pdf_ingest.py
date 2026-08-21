"""单文档复杂真实 PDF：中国平安 2026 年第一季度报告。

docling 解析（含表格清洗入库）→ 结构断言 → 金融术语检索命中。
摄入在子进程中执行（隔离 docling 原生崩溃）。PDF 不在预期路径则 skip。
仅当 RAGENT_LIVE_LLM=1 且 key 齐备时运行。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_llm

PDF_PATH = Path(
    r"C:\Users\Lenovo\Downloads\中国平安：海外监管公告 - 中国平安保险（集团）股份有限公司2026年第一季度报告.pdf")
CHILD_SCRIPT = Path(__file__).parent / "_pdf_ingest_child.py"


@pytest.fixture(scope="module")
def pingan_doc(integration_db, live_env):
    """子进程摄入：DATABASE_URL=ragent_test 经 os.environ 继承（conftest 已设），
    凭据由子进程自行读 .env（主进程哨兵只存在于主进程内存）。"""
    if not PDF_PATH.exists():
        pytest.skip("平安一季报 PDF 不在预期路径：%s" % PDF_PATH)
    from tests.integration.conftest import truncate_corpus

    truncate_corpus(integration_db)
    repo_root = Path(__file__).parent.parent.parent
    import os as _os

    from dotenv import dotenv_values
    child_env = dict(_os.environ)
    child_env["PYTHONPATH"] = str(repo_root)   # 脚本执行不会把 repo 根加入 sys.path
    # root conftest 把哨兵 key 写进了 os.environ 且 env 优先于 .env——
    # 子进程必须用 .env 真实 key 覆盖，否则 embedding 401、摄入必 failed
    vals = dotenv_values(str(repo_root / ".env"))
    for k in ("MINIMAX_API_KEY", "SILICONFLOW_API_KEY"):
        v = (vals.get(k) or "").strip()
        if v:
            child_env[k] = v
    proc = subprocess.run(
        [sys.executable, str(CHILD_SCRIPT), str(PDF_PATH)],
        capture_output=True, text=True, timeout=1800, cwd=str(repo_root),
        env=child_env,
    )
    line = next(
        (line for line in proc.stdout.splitlines() if line.startswith("RESULT_JSON:")), None)
    assert line, (
        "PDF 摄入子进程异常退出（rc=%d，疑似 docling 原生崩溃）；stderr tail:%s%s"
        % (proc.returncode, chr(10), proc.stderr[-800:]))
    res = json.loads(line[len("RESULT_JSON:"):])
    assert res["status"] == "indexed", "PDF 摄入失败: %s" % res
    return res


def test_pdf_ingest_structure(pingan_doc):
    """季度报告（1MB、含大量财务报表表格）应切出足量 chunk。"""
    from app.store import pgvector_store

    chunks = pgvector_store.get_chunks_by_document(pingan_doc["document_id"])
    # docling 对该 PDF 后段页面预处理失败（std::bad_alloc），有效内容约前 10 页
    assert len(chunks) >= 5, "1MB 季报仅切出 %d 块，疑似解析/切分异常" % len(chunks)
    assert all(c["embedding"] is not None for c in chunks)


def test_pdf_table_content_landed(pingan_doc):
    """财务报表词汇应出现在入库 chunk 中（表格清洗后仍可检索）。"""
    from app.store import pgvector_store

    chunks = pgvector_store.get_chunks_by_document(pingan_doc["document_id"])
    joined = " ".join(c["text"] for c in chunks)
    finance_terms = ("营业收入", "净利润", "保险服务收入", "总资产", "股东")
    hits = [t for t in finance_terms if t in joined]
    assert len(hits) >= 2, "财务词汇命中过少（%s），表格内容可能未入库" % (hits,)


async def test_pdf_retrieval_revenue(pingan_doc):
    """营业收入类查询应命中该文档。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "中国平安2026年第一季度营业收入", None, can_read_all=True)
    assert results
    top_docs = {r.document_id for r in results[:5]}
    assert pingan_doc["document_id"] in top_docs


async def test_pdf_retrieval_profit(pingan_doc):
    """净利润类查询应命中该文档。"""
    from app.core.retrieval import retrieval_engine

    results = await retrieval_engine.retrieve(
        "中国平安2026年第一季度净利润表现", None, can_read_all=True)
    assert results
    top_docs = {r.document_id for r in results[:5]}
    assert pingan_doc["document_id"] in top_docs

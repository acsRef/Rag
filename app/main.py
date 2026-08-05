from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.auth import router as auth_router
from app.store.db import engine
from app.api.admin import router as admin_router
from app.api.kb import router as kb_router
from app.api.diagnostics import router as diag_router
from app.store.db import init_db, get_session, Document
from app.store.auth_store import seed_defaults
from app.core.pii_rules import seed_pii_rules
from app.config import settings
import asyncio
import logging
import uvicorn

app = FastAPI(title="RAGent Py", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(kb_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(diag_router)

# 诊断遥测不再以静态目录暴露（曾无鉴权泄漏全量用户 query）：
# JSON 一律经 /api/v1/diag/*（admin-only）访问；查看器 tools/diagnostics.html 从磁盘打开。


@app.on_event("startup")
def startup():
    from app.core.logging import setup_logging
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("RAGent-py starting up")
    if settings.jwt_secret == "change-me-to-a-random-secret":
        raise RuntimeError("请设置环境变量 JWT_SECRET，不要使用默认值")
    if settings.pii_encryption_key == "change-me-to-a-random-key":
        raise RuntimeError("请设置环境变量 PII_ENCRYPTION_KEY，不要使用默认值")
    # 保存主事件循环引用,供后台 ingestion 线程 emit SSE 事件
    from app.api.documents import set_main_loop
    set_main_loop(asyncio.get_event_loop())
    init_db()
    seed_defaults()
    seed_pii_rules()
    # 恢复上次中断时遗留在 processing 状态的文档
    session = None
    try:
        session = get_session()
        stuck = session.query(Document).filter(Document.status.in_(["processing", "indexing"])).all()
        if stuck:
            logger.warning(
                "Recovering %d documents stuck in processing/indexing state (previous restart)",
                len(stuck),
            )
            for doc in stuck:
                doc.status = "failed"
                doc.error_message = "服务重启中断"
            session.commit()
    except Exception:
        if session:
            session.rollback()
        logger.exception("Failed to recover stuck documents")
    finally:
        if session:
            session.close()
    logger.info("RAGent-py startup complete")


@app.get("/health")
def health():
    """健康探针：DB 可达返回 ok；DB 故障返回 degraded 让 docker healthcheck / 监控
    能识别，不被「应用进程存活」误判为健康。

    DB-4：补一个轻量 SELECT 1 探针。失败时 status=degraded 但接口 200
    —— 不把 health 探针搞成纯连通信号，避免上游负载均衡把 DB 故障节点直接
    摘掉（DB 故障下应用其它降级路径仍可服务）。
    """
    db_ok = True
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
    }


if __name__ == "__main__":
    import sys
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload="--reload" in sys.argv,
    )

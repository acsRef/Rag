from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.kb import router as kb_router
from app.store.db import init_db
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
    logger.info("RAGent-py startup complete")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )

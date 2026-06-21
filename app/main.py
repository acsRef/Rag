from fastapi import FastAPI
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.kb import router as kb_router
from app.store.db import init_db
from app.store.auth_store import seed_defaults
from app.config import settings
import uvicorn

app = FastAPI(title="RAGent Py", version="0.2.0")
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(kb_router)
app.include_router(documents_router)
app.include_router(chat_router)


@app.on_event("startup")
def startup():
    init_db()
    seed_defaults()


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

from fastapi import FastAPI
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.store.db import init_db
from app.config import settings
import uvicorn

app = FastAPI(title="RAGent Py", version="0.1.0")
app.include_router(chat_router)
app.include_router(documents_router)


@app.on_event("startup")
def startup():
    init_db()


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

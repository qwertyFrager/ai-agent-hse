import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Base, engine, ensure_database_schema, get_session
from .history import request_history
from .indexer import index_path
from .models import Chunk, Doc
from .rag import answer_question
from .schemas import (
    AskRequest,
    AskResponse,
    DocumentItem,
    HealthResponse,
    HistoryItem,
    IndexRequest,
    IndexResponse,
    StatsResponse,
)

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="RAG Учебный офис", version="0.3.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _should_auto_index() -> bool:
    return os.getenv("AUTO_INDEX_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}


def _dump_model(item):
    return item.model_dump() if hasattr(item, "model_dump") else item.dict()


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_database_schema()
    if _should_auto_index():
        data_dir = os.getenv("DATA_DIR", "./docs")
        logger.info("Auto indexing from %s", data_dir)
        index_path(data_dir)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True)


@app.get("/api/history", response_model=list[HistoryItem])
def history() -> list[HistoryItem]:
    return [HistoryItem(**item) for item in request_history.list()]


@app.get("/api/stats", response_model=StatsResponse)
def stats(db: Session = Depends(get_session)) -> StatsResponse:
    docs_count = db.scalar(select(func.count()).select_from(Doc)) or 0
    chunks_count = db.scalar(select(func.count()).select_from(Chunk)) or 0
    return StatsResponse(
        docs_count=docs_count,
        chunks_count=chunks_count,
        recent_requests=request_history.count(),
    )


@app.get("/api/docs", response_model=list[DocumentItem])
def documents(db: Session = Depends(get_session)) -> list[DocumentItem]:
    rows = db.execute(select(Doc).where(Doc.status == "active").order_by(Doc.title)).scalars()
    return [
        DocumentItem(
            title=doc.title,
            description=doc.description,
            file_path=doc.file_path,
            file_type=doc.file_type,
            updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
        )
        for doc in rows
    ]


@app.post("/index", response_model=IndexResponse)
def index(payload: IndexRequest) -> IndexResponse:
    path = payload.path or os.getenv("DATA_DIR", "./docs")
    return IndexResponse(**index_path(path))


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_session)) -> AskResponse:
    question = " ".join(payload.question.split())
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    response = answer_question(db, question, payload.top_k)
    request_history.add(
        {
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": question,
            "answer_preview": response.answer[:300],
            "sources": [_dump_model(source) for source in response.sources],
        }
    )
    return response

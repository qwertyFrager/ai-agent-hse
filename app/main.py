import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .admin_auth import (
    COOKIE_NAME,
    admin_auth_enabled,
    authenticate_admin,
    create_admin_session,
    get_authenticated_admin,
    require_admin,
    require_admin_configured,
)
from .db import Base, SessionLocal, engine, ensure_database_schema, get_session
from .file_access import can_preview_in_browser, guess_media_type
from .history import request_history
from .indexer import (
    SUPPORTED_EXTENSIONS,
    get_data_dir,
    get_storage_path,
    index_file,
    index_path,
    normalize_stored_doc_paths,
    resolve_storage_path,
)
from .models import Chunk, Doc
from .rag import answer_question
from .schemas import (
    AdminDocumentItem,
    AdminDocumentUpdateRequest,
    AdminLoginRequest,
    AdminSessionResponse,
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


def _serialize_doc(doc: Doc) -> AdminDocumentItem:
    return AdminDocumentItem(
        id=str(doc.id),
        can_preview=can_preview_in_browser(doc.file_path),
        title=doc.title,
        description=doc.description,
        file_path=doc.file_path,
        file_type=doc.file_type,
        updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
    )


def _sanitize_filename(filename: str) -> str:
    candidate = Path(filename or "").name.strip()
    if not candidate or candidate in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return candidate


def _ensure_supported_extension(filename: str) -> None:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Supported: {supported}",
        )


def _ensure_data_dir() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _get_doc_or_404(db: Session, doc_id: UUID) -> Doc:
    doc = db.get(Doc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _dump_model(item):
    return item.model_dump() if hasattr(item, "model_dump") else item.dict()


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_database_schema()
    with SessionLocal() as session:
        normalize_stored_doc_paths(session)
    if _should_auto_index():
        data_dir = os.getenv("DATA_DIR", "./docs")
        logger.info("Auto indexing from %s", data_dir)
        index_path(data_dir)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


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
            id=str(doc.id),
            can_preview=can_preview_in_browser(doc.file_path),
            title=doc.title,
            description=doc.description,
            file_path=doc.file_path,
            file_type=doc.file_type,
            updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
        )
        for doc in rows
    ]


@app.get("/api/docs/{doc_id}/file")
def document_file(doc_id: UUID, download: bool = False, db: Session = Depends(get_session)) -> FileResponse:
    doc = _get_doc_or_404(db, doc_id)
    if doc.status != "active":
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = resolve_storage_path(doc.file_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found")

    media_type = guess_media_type(file_path)
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type=disposition,
    )


@app.post("/index", response_model=IndexResponse)
def index(payload: IndexRequest) -> IndexResponse:
    path = payload.path or os.getenv("DATA_DIR", "./docs")
    return IndexResponse(**index_path(path))


@app.post("/admin/api/reindex", response_model=IndexResponse)
def admin_reindex(
    payload: IndexRequest,
    _: str = Depends(require_admin),
) -> IndexResponse:
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


@app.get("/admin/session", response_model=AdminSessionResponse)
def admin_session(request: Request) -> AdminSessionResponse:
    username = get_authenticated_admin(request)
    return AdminSessionResponse(
        enabled=admin_auth_enabled(),
        authenticated=bool(username),
        username=username or "",
    )


@app.post("/admin/login", response_model=AdminSessionResponse)
def admin_login(payload: AdminLoginRequest, response: Response) -> AdminSessionResponse:
    require_admin_configured()
    username = payload.username.strip()
    if not authenticate_admin(username, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    response.set_cookie(
        key=COOKIE_NAME,
        value=create_admin_session(username),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 8,
    )
    return AdminSessionResponse(enabled=True, authenticated=True, username=username)


@app.post("/admin/logout", response_model=AdminSessionResponse)
def admin_logout(response: Response) -> AdminSessionResponse:
    response.delete_cookie(COOKIE_NAME, samesite="lax")
    return AdminSessionResponse(
        enabled=admin_auth_enabled(),
        authenticated=False,
        username="",
    )


@app.get("/admin/api/docs", response_model=list[AdminDocumentItem])
def admin_documents(
    _: str = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AdminDocumentItem]:
    rows = db.execute(select(Doc).where(Doc.status == "active").order_by(Doc.updated_at.desc())).scalars()
    return [_serialize_doc(doc) for doc in rows]


@app.post("/admin/api/docs", response_model=AdminDocumentItem)
async def admin_create_document(
    _: str = Depends(require_admin),
    db: Session = Depends(get_session),
    file: UploadFile = File(...),
    description: str = Form(""),
) -> AdminDocumentItem:
    data_dir = _ensure_data_dir()
    filename = _sanitize_filename(file.filename or "")
    _ensure_supported_extension(filename)

    target_path = (data_dir / filename).resolve()
    if target_path.exists():
        raise HTTPException(status_code=409, detail="File already exists")

    try:
        with target_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        index_file(target_path)
    except ValueError as exc:
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        if target_path.exists():
            target_path.unlink()
        raise
    finally:
        await file.close()

    storage_path = get_storage_path(target_path)
    doc = db.execute(select(Doc).where(Doc.file_path == storage_path)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=500, detail="Document was uploaded but not indexed")

    if description.strip():
        doc.description = description.strip()
        db.commit()
        db.refresh(doc)

    return _serialize_doc(doc)


@app.patch("/admin/api/docs/{doc_id}", response_model=AdminDocumentItem)
def admin_update_document(
    doc_id: UUID,
    payload: AdminDocumentUpdateRequest,
    _: str = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AdminDocumentItem:
    doc = _get_doc_or_404(db, doc_id)

    if payload.title is not None:
        new_title = payload.title.strip()
        if not new_title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        doc.title = new_title

    if payload.description is not None:
        doc.description = payload.description.strip()

    db.commit()
    db.refresh(doc)
    return _serialize_doc(doc)


@app.delete("/admin/api/docs/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_document(
    doc_id: UUID,
    _: str = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    doc = _get_doc_or_404(db, doc_id)
    file_path = resolve_storage_path(doc.file_path)

    db.delete(doc)
    db.commit()

    if file_path.exists() and file_path.is_file():
        file_path.unlink()

    return Response(status_code=status.HTTP_204_NO_CONTENT)

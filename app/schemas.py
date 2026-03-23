from typing import List, Optional

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(8, ge=1, le=20)


class SourceItem(BaseModel):
    title: str
    description: str
    file_path: str
    chunk_index: int
    snippet: str


class AskResponse(BaseModel):
    answer: str
    sources: List[SourceItem]


class IndexRequest(BaseModel):
    path: Optional[str] = None


class IndexResponse(BaseModel):
    indexed_docs: int
    updated_docs: int
    skipped_docs: int


class HealthResponse(BaseModel):
    ok: bool = True


class HistoryItem(BaseModel):
    asked_at: str
    question: str
    answer_preview: str
    sources: List[SourceItem]


class StatsResponse(BaseModel):
    docs_count: int
    chunks_count: int
    recent_requests: int


class DocumentItem(BaseModel):
    title: str
    description: str
    file_path: str
    file_type: str
    updated_at: str

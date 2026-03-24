import logging
import re
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .file_access import can_preview_in_browser
from .llm import chat_completion, llm_enabled
from .models import Doc
from .schemas import AskResponse, SourceItem
from .search import search_chunks
from .utils_text import normalize_whitespace

logger = logging.getLogger(__name__)

SEARCH_STOPWORDS = {
    "а",
    "в",
    "во",
    "все",
    "где",
    "для",
    "делать",
    "и",
    "или",
    "как",
    "какая",
    "какие",
    "какой",
    "когда",
    "ли",
    "мне",
    "на",
    "надо",
    "не",
    "нужно",
    "о",
    "по",
    "под",
    "почему",
    "про",
    "проходит",
    "проходят",
    "проходить",
    "с",
    "со",
    "хочу",
    "что",
    "это",
}


def _normalize_search_token(token: str) -> str:
    token = token.lower().strip(".")
    if token.startswith("перев"):
        return "перевод"
    if token.startswith("поступ"):
        return "поступление"
    if token.startswith("академ"):
        return "академический"
    return token


def _build_context(rows) -> str:
    parts: List[str] = []
    for row in rows:
        parts.append(
            "\n".join(
                [
                    f"[DocTitle: {row['title']}]",
                    f"[DocDescription: {row['description']}]",
                    f"[Path: {row['file_path']}]",
                    f"[ChunkIndex: {row['chunk_index']}]",
                    row["content"],
                ]
            )
        )
    return "\n\n".join(parts)[:16000]


def _rows_to_sources(rows) -> List[SourceItem]:
    sources: List[SourceItem] = []
    seen_doc_ids = set()
    for row in rows:
        doc_id = row["doc_id"]
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        sources.append(
            SourceItem(
                doc_id=doc_id,
                can_preview=can_preview_in_browser(row["file_path"]),
                title=row["title"],
                description=row["description"],
                file_path=row["file_path"],
                chunk_index=row["chunk_index"],
                snippet=normalize_whitespace(row["content"])[:300],
            )
        )
    return sources


def _library_overview(session: Session, limit: int = 12) -> str:
    docs = session.execute(
        select(Doc).where(Doc.status == "active").order_by(Doc.updated_at.desc()).limit(limit)
    ).scalars()
    lines = []
    for doc in docs:
        lines.append(
            f"- {doc.title} ({doc.file_type}): {normalize_whitespace(doc.description)[:280]}"
        )
    return "\n".join(lines)


def _search_queries(question: str) -> List[str]:
    queries = [normalize_whitespace(question)]
    tokens = re.findall(r"[0-9A-Za-zА-Яа-я.]+", question.lower())
    filtered = [
        _normalize_search_token(token)
        for token in tokens
        if token not in SEARCH_STOPWORDS and (len(token) > 2 or "." in token)
    ]
    if filtered:
        queries.append(" ".join(filtered[:8]))

    if "перевод" in filtered:
        queries.extend(["перевод", "порядок перевода", "перевод студентов"])

    for token in filtered:
        if len(token) >= 4:
            queries.append(token)

    seen = set()
    result = []
    for query in queries:
        if query and query not in seen:
            seen.add(query)
            result.append(query)
    return result


def _retrieve_rows(session: Session, question: str, top_k: int):
    merged = {}
    for position, query in enumerate(_search_queries(question)):
        rows = search_chunks(session, query, top_k)
        for row in rows:
            key = (row["file_path"], row["chunk_index"])
            score = float(row["rank"]) + (0.01 if position > 0 else 0.0)
            candidate = dict(row)
            candidate["rank"] = score
            if key not in merged or merged[key]["rank"] < score:
                merged[key] = candidate
    return sorted(merged.values(), key=lambda item: item["rank"], reverse=True)[:top_k]


def _fallback_answer(question: str, sources: List[SourceItem]) -> str:
    if sources:
        lines = [
            f"Не удалось обратиться к LLM. Ниже найденные материалы по вопросу: {question}",
        ]
        for index, source in enumerate(sources, start=1):
            lines.append(
                f"{index}. {source.title}: {source.snippet}"
            )
        return "\n".join(lines)
    return (
        "Не удалось обратиться к LLM и в локальной базе не нашлось прямых совпадений. "
        "Проверьте настройки модели или уточните вопрос."
    )


def _system_prompt() -> str:
    return (
        "Ты AI-агент учебного офиса. Всегда отвечай живым понятным языком и помогай пользователю, "
        "даже если точных данных в локальной базе нет.\n"
        "Правила ответа:\n"
        "1. Если есть релевантные фрагменты из локальной базы, опирайся на них как на подтвержденные источники.\n"
        "2. Если данных недостаточно, прямо скажи, чего именно не хватает, и отдели общую рекомендацию от подтвержденных фактов.\n"
        "3. Если пользователь задает свободный вопрос не по базе, все равно ответь как полезный ассистент, но не выдумывай наличие локальных подтверждений.\n"
        "4. Если используешь сведения из базы, упомяни названия документов естественно в тексте.\n"
        "5. Не пиши, что ты не можешь помочь только потому, что поиск ничего не нашел. Вместо этого объясни, что подтвержденного контекста в базе нет, и дай лучший возможный ответ."
    )


def _user_prompt(question: str, context: str, library_overview: str) -> str:
    context_block = context if context else "Релевантные фрагменты не найдены."
    return (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Каталог локальных документов:\n{library_overview or 'Каталог пуст.'}\n\n"
        f"Извлеченные фрагменты:\n{context_block}\n\n"
        "Сначала определи, есть ли подтвержденные факты в извлеченных фрагментах. "
        "Если да, ответь по ним. Если нет, ответь все равно полезно, но явно обозначь, что точного подтверждения в локальной базе нет."
    )


def answer_question(session: Session, question: str, top_k: int = 8) -> AskResponse:
    rows = _retrieve_rows(session, question, top_k)
    sources = _rows_to_sources(rows)

    if not llm_enabled():
        return AskResponse(answer=_fallback_answer(question, sources), sources=sources)

    answer = chat_completion(
        _system_prompt(),
        _user_prompt(question, _build_context(rows), _library_overview(session)),
        temperature=0.35,
        max_tokens=700,
    )
    if not answer:
        answer = _fallback_answer(question, sources)

    return AskResponse(answer=answer, sources=sources)

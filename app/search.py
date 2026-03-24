import re

from sqlalchemy import text


FTS_QUERY = text(
    """
    SELECT
        c.id,
        d.id::text AS doc_id,
        c.chunk_index,
        c.content,
        d.title,
        d.description,
        d.file_path,
        ts_rank_cd(
            c.tsv || to_tsvector('russian', coalesce(d.title, '') || ' ' || coalesce(d.description, '')),
            plainto_tsquery('russian', :query)
        ) AS rank
    FROM chunks c
    JOIN docs d ON d.id = c.doc_id
    WHERE (
        c.tsv || to_tsvector('russian', coalesce(d.title, '') || ' ' || coalesce(d.description, ''))
    ) @@ plainto_tsquery('russian', :query)
    ORDER BY rank DESC, d.updated_at DESC, c.chunk_index ASC
    LIMIT :top_k
    """
)

LIKE_QUERY = text(
    """
    SELECT
        c.id,
        d.id::text AS doc_id,
        c.chunk_index,
        c.content,
        d.title,
        d.description,
        d.file_path,
        0.0 AS rank
    FROM chunks c
    JOIN docs d ON d.id = c.doc_id
    WHERE
        lower(c.content) LIKE :pattern OR
        lower(d.title) LIKE :pattern OR
        lower(d.description) LIKE :pattern
    ORDER BY d.updated_at DESC, c.chunk_index ASC
    LIMIT :top_k
    """
)


def search_chunks(session, query: str, top_k: int = 8):
    cleaned_query = " ".join(query.split())
    if not cleaned_query:
        return []

    rows = session.execute(
        FTS_QUERY,
        {"query": cleaned_query, "top_k": top_k},
    ).mappings().all()
    if rows:
        return rows

    pattern = f"%{cleaned_query.lower()}%"
    rows = session.execute(
        LIKE_QUERY,
        {"pattern": pattern, "top_k": top_k},
    ).mappings().all()
    if rows:
        return rows

    tokens = [
        token
        for token in re.findall(r"[0-9A-Za-zА-Яа-я.]+", cleaned_query.lower())
        if len(token) >= 4
    ]
    merged = {}
    for token in tokens[:6]:
        token_rows = session.execute(
            LIKE_QUERY,
            {"pattern": f"%{token}%", "top_k": top_k},
        ).mappings().all()
        for row in token_rows:
            key = (row["doc_id"], row["chunk_index"])
            if key not in merged:
                candidate = dict(row)
                candidate["rank"] = 1.0
                merged[key] = candidate
            else:
                merged[key]["rank"] += 1.0

    return sorted(merged.values(), key=lambda item: item["rank"], reverse=True)[:top_k]

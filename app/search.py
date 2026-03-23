from sqlalchemy import text


FTS_QUERY = text(
    """
    SELECT
        c.id,
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
    return session.execute(
        LIKE_QUERY,
        {"pattern": pattern, "top_k": top_k},
    ).mappings().all()

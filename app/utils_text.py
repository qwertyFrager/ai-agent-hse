import re
from typing import List


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_paragraphs(text: str) -> List[str]:
    text = normalize_whitespace(text)
    parts = re.split(r"\n\s*\n", text)
    if len(parts) == 1:
        parts = text.split("\n")
    paragraphs = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip()
        if part:
            paragraphs.append(part)
    return paragraphs


def chunk_text(
    text: str, min_size: int = 1200, max_size: int = 1800, overlap: int = 200
) -> List[str]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if not current:
            current = para
            continue

        if len(current) < min_size:
            current = f"{current}\n{para}"
            continue

        if len(current) + 1 + len(para) <= max_size:
            current = f"{current}\n{para}"
            continue

        chunks.append(current)
        if overlap > 0:
            tail = current[-overlap:]
            current = f"{tail}\n{para}"
        else:
            current = para

    if current:
        chunks.append(current)

    return chunks

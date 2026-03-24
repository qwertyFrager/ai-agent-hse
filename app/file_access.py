import mimetypes
from pathlib import Path


def guess_media_type(file_path: str | Path) -> str:
    media_type, _ = mimetypes.guess_type(str(file_path))
    return media_type or "application/octet-stream"


def can_preview_in_browser(file_path: str | Path) -> bool:
    media_type = guess_media_type(file_path)
    if media_type == "application/pdf":
        return True
    if media_type.startswith("text/"):
        return True
    if media_type.startswith("image/"):
        return True
    return False

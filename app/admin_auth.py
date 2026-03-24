import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import HTTPException, Request, status

COOKIE_NAME = "admin_session"
SESSION_TTL_SECONDS = 60 * 60 * 8


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _get_settings() -> tuple[str, str, str]:
    return (
        os.getenv("ADMIN_USERNAME", "").strip(),
        os.getenv("ADMIN_PASSWORD", ""),
        os.getenv("ADMIN_SESSION_SECRET", "").strip(),
    )


def admin_auth_enabled() -> bool:
    username, password, secret = _get_settings()
    return bool(username and password and secret)


def require_admin_configured() -> None:
    if not admin_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth is not configured",
        )


def authenticate_admin(username: str, password: str) -> bool:
    env_username, env_password, _ = _get_settings()
    if not admin_auth_enabled():
        return False
    return hmac.compare_digest(username, env_username) and hmac.compare_digest(
        password, env_password
    )


def create_admin_session(username: str) -> str:
    _, _, secret = _get_settings()
    payload = {
        "sub": username,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_payload}.{signature}"


def get_authenticated_admin(request: Request) -> Optional[str]:
    if not admin_auth_enabled():
        return None

    token = request.cookies.get(COOKIE_NAME, "")
    if "." not in token:
        return None

    encoded_payload, signature = token.rsplit(".", 1)
    _, expected_password, secret = _get_settings()
    if not expected_password or not secret:
        return None

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    expires_at = int(payload.get("exp", 0))
    username = str(payload.get("sub", "")).strip()
    configured_username, _, _ = _get_settings()
    if not username or username != configured_username or expires_at < int(time.time()):
        return None
    return username


def require_admin(request: Request) -> str:
    require_admin_configured()
    username = get_authenticated_admin(request)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
        )
    return username

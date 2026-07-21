"""In-memory last capture for outbound Telegram photo delivery (#164)."""

from __future__ import annotations

from threading import Lock

_lock = Lock()
_last_capture_data_uri: str | None = None


def store_last_capture(data_uri: str) -> None:
    """Remember the latest tool-produced image data URI for send_telegram_photo."""
    global _last_capture_data_uri
    uri = (data_uri or "").strip()
    if not uri:
        return
    with _lock:
        _last_capture_data_uri = uri


def get_last_capture() -> str | None:
    with _lock:
        return _last_capture_data_uri


def clear_last_capture() -> None:
    """Clear stored capture (for tests)."""
    global _last_capture_data_uri
    with _lock:
        _last_capture_data_uri = None

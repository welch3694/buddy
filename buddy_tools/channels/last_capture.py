"""In-memory last capture for outbound Telegram image delivery (#164, #173)."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

_lock = Lock()
_last: LastCapture | None = None


@dataclass(frozen=True)
class LastCapture:
    """Latest tool-produced capture: preview for analysis, bytes for file send."""

    preview_data_uri: str | None
    delivery_jpeg: bytes
    filename: str


def store_last_capture(
    data_uri: str | None = None,
    *,
    delivery_jpeg: bytes | None = None,
    filename: str = "buddy-capture.jpg",
) -> None:
    """Remember the latest capture for channel(action=send_telegram_photo).

    Prefer explicit ``delivery_jpeg`` (full resolution). If only a data URI is
    provided, decode it as the delivery payload (legacy / preview-only path).
    """
    global _last
    uri = (data_uri or "").strip() or None
    jpeg = delivery_jpeg
    if jpeg is None and uri:
        from buddy_tools.channels.images import data_uri_to_jpeg_bytes

        jpeg = data_uri_to_jpeg_bytes(uri)
    if not jpeg:
        return
    name = (filename or "").strip() or "buddy-capture.jpg"
    with _lock:
        _last = LastCapture(preview_data_uri=uri, delivery_jpeg=jpeg, filename=name)


def get_last_capture() -> str | None:
    """Return the preview data URI when available (legacy helpers / tests)."""
    with _lock:
        return None if _last is None else _last.preview_data_uri


def get_last_capture_delivery() -> tuple[bytes, str] | None:
    """Return ``(jpeg_bytes, filename)`` for Telegram document send."""
    with _lock:
        if _last is None:
            return None
        return _last.delivery_jpeg, _last.filename


def clear_last_capture() -> None:
    """Clear stored capture (for tests)."""
    global _last
    with _lock:
        _last = None

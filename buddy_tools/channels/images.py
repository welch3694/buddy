"""Encode inbound image bytes as JPEG data URIs for vision models."""

from __future__ import annotations

import base64

import cv2
import numpy as np

DEFAULT_MAX_WIDTH = 768
JPEG_QUALITY = 85


def bytes_to_jpeg_data_uri(
    image_bytes: bytes,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    jpeg_quality: int = JPEG_QUALITY,
) -> str:
    """Decode image bytes, resize if needed, and return a base64 JPEG data URI."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Could not decode image bytes")

    height, width = frame.shape[:2]
    if width > max_width:
        scale = max_width / width
        frame = cv2.resize(frame, (max_width, int(height * scale)))

    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    if not ok:
        raise ValueError("Could not encode image as JPEG")

    encoded = base64.b64encode(jpeg.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def data_uri_to_jpeg_bytes(data_uri: str) -> bytes:
    """Decode a ``data:image/...;base64,...`` URI to raw image bytes for Telegram upload."""
    uri = (data_uri or "").strip()
    if not uri.startswith("data:") or "," not in uri:
        raise ValueError("Expected a data:image/...;base64,... URI")

    header, payload = uri.split(",", 1)
    if ";base64" not in header.lower():
        raise ValueError("Expected a base64 data URI")
    if "image/" not in header.lower():
        raise ValueError("Expected an image data URI")

    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise ValueError(f"Could not decode base64 image payload: {exc}") from exc
    if not raw:
        raise ValueError("Decoded image payload is empty")
    return raw

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

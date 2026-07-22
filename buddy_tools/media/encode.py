"""Shared JPEG encode helpers for camera/screen dual capture (#173)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np

PREVIEW_JPEG_QUALITY = 85
DELIVERY_JPEG_QUALITY = 95


@dataclass(frozen=True)
class DualJpegCapture:
    """Preview data URI for LLM analysis plus native-resolution delivery bytes."""

    preview_data_uri: str
    delivery_jpeg: bytes
    width: int
    height: int


def encode_preview_and_delivery(
    frame: np.ndarray,
    *,
    max_width: int,
    preview_quality: int = PREVIEW_JPEG_QUALITY,
    delivery_quality: int = DELIVERY_JPEG_QUALITY,
) -> DualJpegCapture:
    """Encode native JPEG for delivery and a width-capped preview for analysis."""
    height, width = frame.shape[:2]

    ok, delivery = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, delivery_quality],
    )
    if not ok:
        raise RuntimeError("Could not encode full-resolution JPEG")

    preview_frame = frame
    if width > max_width:
        scale = max_width / width
        preview_frame = cv2.resize(frame, (max_width, int(height * scale)))

    ok, preview = cv2.imencode(
        ".jpg",
        preview_frame,
        [cv2.IMWRITE_JPEG_QUALITY, preview_quality],
    )
    if not ok:
        raise RuntimeError("Could not encode preview JPEG")

    encoded = base64.b64encode(preview.tobytes()).decode("ascii")
    return DualJpegCapture(
        preview_data_uri=f"data:image/jpeg;base64,{encoded}",
        delivery_jpeg=delivery.tobytes(),
        width=width,
        height=height,
    )

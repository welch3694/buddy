"""Capture frames from the default system webcam."""

from __future__ import annotations

import base64
import logging
import sys

import cv2

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_INDEX = 0
DEFAULT_MAX_WIDTH = 768
JPEG_QUALITY = 85
WARMUP_FRAMES = 3


def _open_camera(device_index: int) -> cv2.VideoCapture:
    if sys.platform == "win32":
        cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
    return cv2.VideoCapture(device_index)


def capture_frame(
    device_index: int = DEFAULT_DEVICE_INDEX,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> str:
    """Grab one JPEG frame and return a base64 data URI."""
    cap = _open_camera(device_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera device {device_index}")

    try:
        for _ in range(WARMUP_FRAMES):
            cap.read()

        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("Could not read a frame from the camera")

        height, width = frame.shape[:2]
        if width > max_width:
            scale = max_width / width
            frame = cv2.resize(frame, (max_width, int(height * scale)))

        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("Could not encode camera frame as JPEG")

        encoded = base64.b64encode(jpeg.tobytes()).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{encoded}"
        logger.info("Captured camera frame (%dx%d, %d bytes)", frame.shape[1], frame.shape[0], len(jpeg))
        return data_uri
    finally:
        cap.release()

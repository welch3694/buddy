"""Capture frames from the configured system webcam."""

from __future__ import annotations

import base64
import logging
import os
import sys

import cv2
from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import log_tool_failure, safe_tool_context, tool_error

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_INDEX = 0
DEFAULT_MAX_WIDTH = 768
JPEG_QUALITY = 85
WARMUP_FRAMES = 3

_ENV_CAMERA_DEVICE = "BUDDY_CAMERA_DEVICE"
_ENV_CAMERA_NAME = "BUDDY_CAMERA_NAME"

CAMERA_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="capture_camera",
        description=(
            "Capture a photo from the user's configured webcam for visual analysis. "
            "Call when the user asks what you see, to look at something, or to describe "
            "their surroundings."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
]


def _list_camera_devices() -> list[tuple[int, str]]:
    """Return (index, name) pairs when device names are available."""
    if sys.platform != "win32":
        return []
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError:
        return []

    try:
        graph = FilterGraph()
        return list(enumerate(graph.get_input_devices()))
    except Exception as exc:
        logger.warning("Could not list camera devices: %s", exc)
        return []


def _format_camera_list(devices: list[tuple[int, str]]) -> str:
    if not devices:
        return "(could not list devices; set BUDDY_CAMERA_DEVICE instead)"
    return ", ".join(f"{index}: {name}" for index, name in devices)


def resolve_camera_device_index() -> int:
    """Resolve the camera device index from environment variables."""
    name_raw = os.environ.get(_ENV_CAMERA_NAME, "").strip()
    device_raw = os.environ.get(_ENV_CAMERA_DEVICE, "").strip()

    if name_raw:
        if device_raw:
            logger.info(
                "Both %s and %s are set; using camera name %r",
                _ENV_CAMERA_NAME,
                _ENV_CAMERA_DEVICE,
                name_raw,
            )

        devices = _list_camera_devices()
        target = name_raw.casefold()
        for index, device_name in devices:
            if device_name.casefold() == target:
                logger.info(
                    "Using camera %r (device %d) from %s",
                    device_name,
                    index,
                    _ENV_CAMERA_NAME,
                )
                return index

        partial = [
            (index, device_name)
            for index, device_name in devices
            if target in device_name.casefold()
        ]
        if len(partial) == 1:
            index, device_name = partial[0]
            logger.info(
                "Using camera %r (device %d) from %s",
                device_name,
                index,
                _ENV_CAMERA_NAME,
            )
            return index
        if len(partial) > 1:
            raise ValueError(
                f"Camera name {name_raw!r} is ambiguous; matches: {_format_camera_list(partial)}"
            )

        raise ValueError(
            f"Camera {name_raw!r} not found. Available: {_format_camera_list(devices)}"
        )

    if device_raw:
        try:
            index = int(device_raw)
        except ValueError as exc:
            raise ValueError(f"{_ENV_CAMERA_DEVICE} must be an integer, got {device_raw!r}") from exc
        if index < 0:
            raise ValueError(f"{_ENV_CAMERA_DEVICE} must be non-negative, got {index}")
        logger.info("Using camera device %d from %s", index, _ENV_CAMERA_DEVICE)
        return index

    return DEFAULT_DEVICE_INDEX


def _open_camera(device_index: int) -> cv2.VideoCapture:
    if sys.platform == "win32":
        cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
    return cv2.VideoCapture(device_index)


def capture_frame(
    device_index: int | None = None,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> str:
    """Grab one JPEG frame and return a base64 data URI."""
    if device_index is None:
        resolved_index = resolve_camera_device_index()
    else:
        resolved_index = device_index

    cap = _open_camera(resolved_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera device {resolved_index}")

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


def execute_camera_tool() -> ToolExecutionResult:
    try:
        data_uri = capture_frame()
    except Exception as exc:
        log_tool_failure("capture_camera", f"camera capture failed: {exc}", exc=exc)
        return ToolExecutionResult(output=f"Error: camera capture failed: {exc}")
    return ToolExecutionResult(
        output="Camera capture succeeded.",
        image_data_uri=data_uri,
        image_caption="Here is what the camera sees.",
    )

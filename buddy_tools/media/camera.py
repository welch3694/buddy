"""Capture frames from the configured system webcam."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import cv2
from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import log_tool_failure, safe_tool_context, tool_error
from buddy_tools.media.encode import DualJpegCapture, encode_preview_and_delivery

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_INDEX = 0
DEFAULT_MAX_WIDTH = 768
WARMUP_FRAMES = 3
DELIVERY_FILENAME = "buddy-camera.jpg"

_ENV_CAMERA_DEVICE = "BUDDY_CAMERA_DEVICE"
_ENV_CAMERA_NAME = "BUDDY_CAMERA_NAME"

_session_camera_override_index: int | None = None
_session_camera_override_label: str | None = None

CAMERA_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="capture_camera",
        description=(
            "Capture a photo from the user's active webcam for visual analysis. "
            "Call when the user asks what you see, to look at something, or to describe "
            "their surroundings. Use set_active_camera first if they want a different camera."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="list_cameras",
        description=(
            "List available webcam devices by index and name. Call before set_active_camera "
            "when the user asks which cameras are available or wants to pick one by name."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="set_active_camera",
        description=(
            "Switch the active webcam for this session. Use when the user asks to use a "
            "different camera (for example OBS Virtual Camera). Provide camera_name and/or "
            "device_index from list_cameras. Does not change .env or require a restart."
        ),
        parameters={
            "type": "object",
            "properties": {
                "camera_name": {
                    "type": "string",
                    "description": "Camera name or partial name, e.g. OBS Virtual Camera",
                },
                "device_index": {
                    "type": "integer",
                    "description": "Camera device index from list_cameras, e.g. 0 or 3",
                },
            },
        },
    ),
]

CAMERA_TOOL_NAMES = frozenset(tool.name for tool in CAMERA_TOOL_DEFINITIONS)


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


def _resolve_camera_by_name(name_raw: str) -> tuple[int, str]:
    devices = _list_camera_devices()
    target = name_raw.casefold()
    for index, device_name in devices:
        if device_name.casefold() == target:
            return index, device_name

    partial = [
        (index, device_name)
        for index, device_name in devices
        if target in device_name.casefold()
    ]
    if len(partial) == 1:
        index, device_name = partial[0]
        return index, device_name
    if len(partial) > 1:
        raise ValueError(
            f"Camera name {name_raw!r} is ambiguous; matches: {_format_camera_list(partial)}"
        )

    raise ValueError(
        f"Camera {name_raw!r} not found. Available: {_format_camera_list(devices)}"
    )


def _resolve_camera_by_index(index: int) -> tuple[int, str | None]:
    if index < 0:
        raise ValueError(f"device_index must be non-negative, got {index}")

    for device_index, device_name in _list_camera_devices():
        if device_index == index:
            return index, device_name
    return index, None


def resolve_camera_selector(
    *,
    camera_name: str | None = None,
    device_index: int | None = None,
) -> tuple[int, str | None]:
    """Resolve a camera by name or device index."""
    name_raw = (camera_name or "").strip()
    if name_raw:
        if device_index is not None:
            logger.info(
                "Both camera_name and device_index provided; using camera name %r",
                name_raw,
            )
        index, device_name = _resolve_camera_by_name(name_raw)
        return index, device_name

    if device_index is not None:
        return _resolve_camera_by_index(device_index)

    raise ValueError("Provide camera_name or device_index")


def set_session_camera_override(device_index: int, label: str | None = None) -> None:
    """Set the active camera for the current session."""
    global _session_camera_override_index, _session_camera_override_label
    _session_camera_override_index = device_index
    _session_camera_override_label = label


def clear_session_camera_override() -> None:
    """Clear any session camera override."""
    global _session_camera_override_index, _session_camera_override_label
    _session_camera_override_index = None
    _session_camera_override_label = None


def get_session_camera_override() -> tuple[int, str | None] | None:
    """Return the active session camera override, if any."""
    if _session_camera_override_index is None:
        return None
    return _session_camera_override_index, _session_camera_override_label


def resolve_camera_device_index() -> int:
    """Resolve the camera device index from session override or environment."""
    override = get_session_camera_override()
    if override is not None:
        index, label = override
        if label:
            logger.info("Using session camera override %r (device %d)", label, index)
        else:
            logger.info("Using session camera override device %d", index)
        return index

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

        index, device_name = _resolve_camera_by_name(name_raw)
        logger.info(
            "Using camera %r (device %d) from %s",
            device_name,
            index,
            _ENV_CAMERA_NAME,
        )
        return index

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
) -> DualJpegCapture:
    """Grab one frame; return preview URI for analysis and full JPEG for delivery."""
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

        captured = encode_preview_and_delivery(frame, max_width=max_width)
        logger.info(
            "Captured camera frame (%dx%d, preview+delivery %d bytes)",
            captured.width,
            captured.height,
            len(captured.delivery_jpeg),
        )
        return captured
    finally:
        cap.release()


def _format_active_camera_summary() -> str:
    override = get_session_camera_override()
    if override is None:
        try:
            index = resolve_camera_device_index()
        except ValueError:
            return "Active camera: unknown"
        return f"Active camera: device {index} (from startup configuration)"

    index, label = override
    if label:
        return f"Active camera: {label} (device {index})"
    return f"Active camera: device {index}"


def execute_list_cameras_tool() -> ToolExecutionResult:
    devices = _list_camera_devices()
    if devices:
        output = f"Available cameras: {_format_camera_list(devices)}. {_format_active_camera_summary()}."
    else:
        output = (
            "Could not list camera names on this system. "
            "Try device_index values starting at 0. "
            f"{_format_active_camera_summary()}."
        )
    return ToolExecutionResult(output=output)


def execute_set_active_camera_tool(args: dict[str, Any]) -> ToolExecutionResult:
    camera_name = args.get("camera_name")
    device_index = args.get("device_index")
    if camera_name is None and device_index is None:
        return tool_error(
            "set_active_camera",
            "provide camera_name or device_index",
            context=safe_tool_context(args),
        )

    try:
        if device_index is not None:
            device_index = int(device_index)
        index, label = resolve_camera_selector(camera_name=camera_name, device_index=device_index)
    except (TypeError, ValueError) as exc:
        return tool_error("set_active_camera", str(exc), context=safe_tool_context(args))

    set_session_camera_override(index, label)
    if label:
        output = f"Switched to camera {label} (device {index}) for this session."
    else:
        output = f"Switched to camera device {index} for this session."
    logger.info(output)
    return ToolExecutionResult(output=output)


def execute_camera_tool() -> ToolExecutionResult:
    try:
        captured = capture_frame()
    except Exception as exc:
        log_tool_failure("capture_camera", f"camera capture failed: {exc}", exc=exc)
        return ToolExecutionResult(output=f"Error: camera capture failed: {exc}")
    return ToolExecutionResult(
        output="Camera capture succeeded.",
        image_data_uri=captured.preview_data_uri,
        image_caption="Here is what the camera sees.",
        image_delivery_bytes=captured.delivery_jpeg,
        image_delivery_filename=DELIVERY_FILENAME,
    )

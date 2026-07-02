"""Capture screenshots from the user's display."""

from __future__ import annotations

import base64
import logging

import cv2
import mss
import numpy as np
from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.result import ToolExecutionResult

logger = logging.getLogger(__name__)

DEFAULT_MAX_WIDTH = 1280
JPEG_QUALITY = 85

SCREEN_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="capture_screen",
        description=(
            "Capture a screenshot of the user's screen for visual analysis. "
            "Call when the user asks what is on their screen, to read something visible, "
            "or to help with what they are looking at on the display."
        ),
        parameters={
            "type": "object",
            "properties": {
                "monitor": {
                    "type": "integer",
                    "description": "Monitor index (0 = primary display, 1 = second display, etc.)",
                }
            },
        },
    ),
]


def capture_screen(monitor: int = 0, max_width: int = DEFAULT_MAX_WIDTH) -> str:
    """Grab one JPEG screenshot and return a base64 data URI."""
    with mss.mss() as sct:
        monitors = sct.monitors[1:]
        if not monitors:
            raise RuntimeError("No displays found")
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"Invalid monitor index {monitor}; available: 0-{len(monitors) - 1}")

        screenshot = sct.grab(monitors[monitor])
        frame = np.array(screenshot)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        height, width = frame.shape[:2]
        if width > max_width:
            scale = max_width / width
            frame = cv2.resize(frame, (max_width, int(height * scale)))

        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("Could not encode screen capture as JPEG")

        encoded = base64.b64encode(jpeg.tobytes()).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{encoded}"
        logger.info(
            "Captured screen (monitor %d, %dx%d, %d bytes)",
            monitor,
            frame.shape[1],
            frame.shape[0],
            len(jpeg),
        )
        return data_uri


def execute_screen_tool(args: dict) -> ToolExecutionResult:
    monitor = int(args.get("monitor", 0))
    try:
        data_uri = capture_screen(monitor=monitor)
    except Exception as exc:
        logger.exception("Screen capture failed")
        return ToolExecutionResult(output=f"Error: screen capture failed: {exc}")
    return ToolExecutionResult(
        output="Screen capture succeeded.",
        image_data_uri=data_uri,
        image_caption="Here is what is on the user's screen.",
    )

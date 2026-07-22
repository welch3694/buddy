"""Capture screenshots from the user's display."""

from __future__ import annotations

import logging

import cv2
import mss
import numpy as np
from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import log_tool_failure
from buddy_tools.media.encode import DualJpegCapture, encode_preview_and_delivery

logger = logging.getLogger(__name__)

DEFAULT_MAX_WIDTH = 1280
DELIVERY_FILENAME = "buddy-screen.jpg"

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


def capture_screen(monitor: int = 0, max_width: int = DEFAULT_MAX_WIDTH) -> DualJpegCapture:
    """Grab one screenshot; return preview URI for analysis and full JPEG for delivery."""
    with mss.mss() as sct:
        monitors = sct.monitors[1:]
        if not monitors:
            raise RuntimeError("No displays found")
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"Invalid monitor index {monitor}; available: 0-{len(monitors) - 1}")

        screenshot = sct.grab(monitors[monitor])
        frame = np.array(screenshot)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        captured = encode_preview_and_delivery(frame, max_width=max_width)
        logger.info(
            "Captured screen (monitor %d, %dx%d, delivery %d bytes)",
            monitor,
            captured.width,
            captured.height,
            len(captured.delivery_jpeg),
        )
        return captured


def execute_screen_tool(args: dict) -> ToolExecutionResult:
    monitor = int(args.get("monitor", 0))
    try:
        captured = capture_screen(monitor=monitor)
    except Exception as exc:
        log_tool_failure(
            "capture_screen",
            f"screen capture failed: {exc}",
            exc=exc,
            context={"monitor": monitor},
        )
        return ToolExecutionResult(output=f"Error: screen capture failed: {exc}")
    return ToolExecutionResult(
        output="Screen capture succeeded.",
        image_data_uri=captured.preview_data_uri,
        image_caption="Here is what is on the user's screen.",
        image_delivery_bytes=captured.delivery_jpeg,
        image_delivery_filename=DELIVERY_FILENAME,
    )

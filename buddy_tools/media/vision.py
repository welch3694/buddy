"""Vision tool group: webcam and screen capture."""

from __future__ import annotations

from buddy_tools.core.groups import ToolGroup
from buddy_tools.media.camera import CAMERA_TOOL_DEFINITIONS
from buddy_tools.media.screen import SCREEN_TOOL_DEFINITIONS


def build_vision_instructions() -> str:
    return (
        "You can see through the user's webcam with capture_camera. Call it when they ask what you "
        "see, what is in front of you, to look at something, or to describe their surroundings. "
        "If they want a different webcam (for example OBS Virtual Camera), call list_cameras then "
        "set_active_camera before capture_camera. After capturing, describe what you see in natural "
        "spoken language without mentioning tools or cameras.\n"
        "You can see the user's screen with capture_screen. Call it when they ask what is on their "
        "screen, to read something visible, or to help with what they are looking at. "
        "After capturing, describe what you see in natural spoken language without mentioning "
        "tools or screenshots."
    )


VISION_TOOL_GROUP = ToolGroup(
    id="vision",
    title="Vision",
    when_to_use=(
        "User asks what you see, to look at the camera/webcam, or what is on their screen."
    ),
    tools=tuple(CAMERA_TOOL_DEFINITIONS) + tuple(SCREEN_TOOL_DEFINITIONS),
    instructions=build_vision_instructions(),
)

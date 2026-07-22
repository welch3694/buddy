"""Vision tool group: webcam and screen capture (consolidated #109)."""

from __future__ import annotations

from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.consolidate import ActionSpec, build_action_tool, resolve_action_args
from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import tool_error
from buddy_tools.media.camera import execute_camera_tool, execute_list_cameras_tool, execute_set_active_camera_tool
from buddy_tools.media.screen import execute_screen_tool

VISION_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(action="capture_camera", legacy_name="capture_camera"),
    ActionSpec(action="list_cameras", legacy_name="list_cameras"),
    ActionSpec(
        action="set_active_camera",
        legacy_name="set_active_camera",
        properties={
            "camera_name": {
                "type": "string",
                "description": "Camera name or partial name, e.g. OBS Virtual Camera",
            },
            "device_index": {
                "type": "integer",
                "description": "Camera device index from vision(action=list_cameras), e.g. 0 or 3",
            },
        },
    ),
    ActionSpec(
        action="capture_screen",
        legacy_name="capture_screen",
        properties={
            "monitor": {
                "type": "integer",
                "description": "Monitor index (0 = primary display, 1 = second display, etc.)",
            }
        },
    ),
)

VISION_TOOL_DEFINITION: RealtimeFunctionTool = build_action_tool(
    name="vision",
    description=(
        "Webcam and screen capture operations. Use action=capture_camera to see through the "
        "user's webcam, action=list_cameras / action=set_active_camera to inspect or switch "
        "cameras, or action=capture_screen to see the user's screen."
    ),
    actions=VISION_ACTIONS,
)

VISION_TOOL_NAMES = frozenset({"vision"})


def build_vision_instructions() -> str:
    return (
        "You can see through the user's webcam with vision(action=capture_camera). Call it when "
        "they ask what you see, what is in front of you, to look at something, or to describe their "
        "surroundings. If they want a different webcam (for example OBS Virtual Camera), call "
        "vision(action=list_cameras) then vision(action=set_active_camera) before capturing. "
        "After capturing, describe what you see in natural spoken language without mentioning "
        "tools or cameras.\n"
        "You can see the user's screen with vision(action=capture_screen). Call it when they ask "
        "what is on their screen, to read something visible, or to help with what they are looking "
        "at. After capturing, describe what you see in natural spoken language without mentioning "
        "tools or screenshots.\n"
        "When they ask you to take a picture or screenshot and send it to them, call the matching "
        "vision capture action first, then channel(action=send_telegram_photo) to deliver the "
        "full-resolution file."
    )


VISION_TOOL_GROUP = ToolGroup(
    id="vision",
    title="Vision",
    when_to_use=(
        "User asks what you see, to look at the camera/webcam, or what is on their screen."
    ),
    tools=(VISION_TOOL_DEFINITION,),
    instructions=build_vision_instructions(),
)


def execute_vision_tool(tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
    if tool_name == "vision":
        resolved = resolve_action_args("vision", args, VISION_ACTIONS)
        if isinstance(resolved, ToolExecutionResult):
            return resolved
        tool_name, args = resolved

    if tool_name == "capture_camera":
        return execute_camera_tool()

    if tool_name == "list_cameras":
        return execute_list_cameras_tool()

    if tool_name == "set_active_camera":
        return execute_set_active_camera_tool(args)

    if tool_name == "capture_screen":
        return execute_screen_tool(args)

    return tool_error(tool_name, f"unknown vision tool {tool_name!r}")

"""Aggregate tool definitions, instructions, and dispatch."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.camera import CAMERA_TOOL_DEFINITIONS, execute_camera_tool
from buddy_tools.listening_pause import build_listening_pause_instructions
from buddy_tools.memory import (
    MEMORY_TOOL_DEFINITIONS,
    MEMORY_TOOL_NAMES,
    build_memory_instructions,
    execute_memory_tool,
    load_memory_summary,
)
from buddy_tools.personality_tools import (
    PERSONALITY_TOOL_DEFINITIONS,
    PERSONALITY_TOOL_NAMES,
    build_personality_instructions,
    execute_personality_tool,
)
from buddy_tools.result import ToolExecutionResult
from buddy_tools.screen import SCREEN_TOOL_DEFINITIONS, execute_screen_tool

logger = logging.getLogger(__name__)

ALL_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = (
    MEMORY_TOOL_DEFINITIONS
    + PERSONALITY_TOOL_DEFINITIONS
    + CAMERA_TOOL_DEFINITIONS
    + SCREEN_TOOL_DEFINITIONS
)


def build_tool_instructions(base_prompt: str, memory_summary: str) -> str:
    return (
        f"{base_prompt.strip()}\n\n"
        f"{build_memory_instructions()}\n\n"
        f"{build_personality_instructions()}\n\n"
        f"{build_listening_pause_instructions()}\n\n"
        "You can see through the user's webcam with capture_camera. Call it when they ask what you "
        "see, what is in front of you, to look at something, or to describe their surroundings. "
        "After capturing, describe what you see in natural spoken language without mentioning "
        "tools or cameras.\n\n"
        "You can see the user's screen with capture_screen. Call it when they ask what is on their "
        "screen, to read something visible, or to help with what they are looking at. "
        "After capturing, describe what you see in natural spoken language without mentioning "
        "tools or screenshots.\n\n"
        "Current memory snapshot:\n"
        f"{memory_summary}"
    )


def execute_tool(
    memory_root: Path,
    tool_name: str,
    arguments_json: str,
    *,
    persona_namespace: str,
) -> ToolExecutionResult:
    try:
        args: dict[str, Any] = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        return ToolExecutionResult(output=f"Error: invalid tool arguments JSON: {exc}")

    try:
        if tool_name == "capture_camera":
            return execute_camera_tool()

        if tool_name == "capture_screen":
            return execute_screen_tool(args)

        if tool_name in MEMORY_TOOL_NAMES:
            return execute_memory_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in PERSONALITY_TOOL_NAMES:
            return execute_personality_tool(tool_name, args)

        return ToolExecutionResult(output=f"Error: unknown tool {tool_name!r}")
    except ValueError as exc:
        return ToolExecutionResult(output=f"Error: {exc}")
    except OSError as exc:
        logger.exception("Tool %s failed", tool_name)
        return ToolExecutionResult(output=f"Error: could not access memory file: {exc}")

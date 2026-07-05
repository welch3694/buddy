"""Aggregate tool definitions, instructions, and dispatch."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai.types.realtime import RealtimeFunctionTool
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.media.camera import CAMERA_TOOL_DEFINITIONS, execute_camera_tool
from buddy_tools.voice.listening_pause import build_listening_pause_instructions
from buddy_tools.episodic.retrieval import (
    EPISODIC_TOOL_DEFINITIONS,
    EPISODIC_TOOL_NAMES,
    build_episodic_instructions,
    execute_episodic_tool,
)
from buddy_tools.memory import (
    MEMORY_TOOL_DEFINITIONS,
    MEMORY_TOOL_NAMES,
    build_memory_instructions,
    execute_memory_tool,
    load_memory_summary,
)
from buddy_tools.personality import get_personality
from buddy_tools.personality.tools import (
    PERSONALITY_TOOL_DEFINITIONS,
    PERSONALITY_TOOL_NAMES,
    build_personality_instructions,
    execute_personality_tool,
)
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import log_tool_failure, safe_tool_context, tool_error
from buddy_tools.media.screen import SCREEN_TOOL_DEFINITIONS, execute_screen_tool
from buddy_tools.skills import (
    SKILL_TOOL_DEFINITIONS,
    SKILL_TOOL_NAMES,
    build_active_skill_context,
    build_skill_instructions,
    execute_skill_tool,
)
from buddy_tools.infra.startup import build_voice_system_prompt
from buddy_tools.timers import (
    TIMER_TOOL_DEFINITIONS,
    TIMER_TOOL_NAMES,
    build_timer_instructions,
    execute_timer_tool,
)

logger = logging.getLogger(__name__)

ALL_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = (
    MEMORY_TOOL_DEFINITIONS
    + EPISODIC_TOOL_DEFINITIONS
    + PERSONALITY_TOOL_DEFINITIONS
    + SKILL_TOOL_DEFINITIONS
    + TIMER_TOOL_DEFINITIONS
    + CAMERA_TOOL_DEFINITIONS
    + SCREEN_TOOL_DEFINITIONS
)


def build_tool_instructions(
    base_prompt: str,
    memory_summary: str,
    *,
    memory_root: Path | None = None,
    persona_namespace: str | None = None,
    personality_id: str | None = None,
    include_full_skill_body: bool = False,
) -> str:
    parts = [
        base_prompt.strip(),
        build_memory_instructions(),
        build_episodic_instructions(),
        build_personality_instructions(),
        build_skill_instructions(),
        build_listening_pause_instructions(),
        build_timer_instructions(),
        (
            "You can see through the user's webcam with capture_camera. Call it when they ask what you "
            "see, what is in front of you, to look at something, or to describe their surroundings. "
            "After capturing, describe what you see in natural spoken language without mentioning "
            "tools or cameras."
        ),
        (
            "You can see the user's screen with capture_screen. Call it when they ask what is on their "
            "screen, to read something visible, or to help with what they are looking at. "
            "After capturing, describe what you see in natural spoken language without mentioning "
            "tools or screenshots."
        ),
    ]

    if memory_root is not None and persona_namespace and personality_id:
        try:
            profile = get_personality(personality_id)
            skill_context = build_active_skill_context(
                memory_root,
                persona_namespace,
                profile,
                include_full_skill_body=include_full_skill_body,
            )
            if skill_context:
                parts.append(skill_context)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Could not build skill context for %r: %s", personality_id, exc)

    parts.append(f"Current memory snapshot:\n{memory_summary}")
    return "\n\n".join(parts)


def refresh_session_instructions(
    runtime_config: RuntimeConfig,
    *,
    memory_root: Path,
    persona_namespace: str,
    personality_id: str,
    include_full_skill_body: bool = False,
) -> None:
    profile = get_personality(personality_id)
    summary = load_memory_summary(memory_root, persona_namespace)
    base = build_voice_system_prompt(profile.prompt)
    runtime_config.session.instructions = build_tool_instructions(
        base,
        summary,
        memory_root=memory_root,
        persona_namespace=persona_namespace,
        personality_id=personality_id,
        include_full_skill_body=include_full_skill_body,
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
        return tool_error(tool_name, f"invalid tool arguments JSON: {exc}")

    try:
        if tool_name == "capture_camera":
            return execute_camera_tool()

        if tool_name == "capture_screen":
            return execute_screen_tool(args)

        if tool_name in MEMORY_TOOL_NAMES:
            return execute_memory_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in EPISODIC_TOOL_NAMES:
            return execute_episodic_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in PERSONALITY_TOOL_NAMES:
            return execute_personality_tool(tool_name, args)

        if tool_name in SKILL_TOOL_NAMES:
            return execute_skill_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in TIMER_TOOL_NAMES:
            return execute_timer_tool(tool_name, args)

        return tool_error(tool_name, f"unknown tool {tool_name!r}")
    except ValueError as exc:
        return tool_error(tool_name, str(exc), context=safe_tool_context(args))
    except OSError as exc:
        log_tool_failure(tool_name, f"could not access memory file: {exc}", exc=exc, context=safe_tool_context(args))
        return ToolExecutionResult(output=f"Error: could not access memory file: {exc}")

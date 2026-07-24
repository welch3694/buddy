"""Aggregate tool definitions, instructions, and dispatch."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai.types.realtime import RealtimeFunctionTool
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.voice.listening_pause import build_listening_pause_instructions
from buddy_tools.voice.barge_in import build_barge_in_instructions
from buddy_tools.episodic.retrieval import (
    EPISODIC_TOOL_GROUP,
    EPISODIC_TOOL_NAMES,
    execute_episodic_tool,
)
from buddy_tools.memory import (
    MEMORY_TOOL_GROUP,
    MEMORY_TOOL_NAMES,
    execute_memory_tool,
    load_memory_summary,
)
from buddy_tools.personality import DEFAULT_PERSONALITY_ID, PersonalityProfile, get_active_personality, get_personality
from buddy_tools.personality.tools import (
    PERSONALITY_TOOL_NAMES,
    PERSONA_ADMIN_TOOL_GROUP,
    PERSONA_TOOL_GROUP,
    execute_personality_tool,
)
from buddy_tools.core.groups import (
    ToolGroup,
    flatten_tool_definitions,
    resolve_visible_groups,
    visible_tool_definitions,
)
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import log_tool_failure, safe_tool_context, tool_error
from buddy_tools.media.vision import VISION_TOOL_GROUP, VISION_TOOL_NAMES, execute_vision_tool
from buddy_tools.skills import (
    SKILL_TOOL_GROUP,
    SKILL_TOOL_NAMES,
    build_active_skill_context,
    execute_skill_tool,
)
from buddy_tools.infra.startup import build_voice_system_prompt
from buddy_tools.timers import (
    TIMER_TOOL_GROUP,
    TIMER_TOOL_NAMES,
    execute_timer_tool,
)
from buddy_tools.themes import (
    THEME_TOOL_GROUP,
    THEME_TOOL_NAMES,
    execute_theme_tool,
)
from buddy_tools.channels.tools import (
    CHANNEL_TOOL_GROUP,
    CHANNEL_TOOL_NAMES,
    execute_channel_tool,
)

# Re-export for bootstrap and other callers.
__all__ = [
    "ALL_TOOL_DEFINITIONS",
    "TOOL_GROUPS",
    "build_tool_instructions",
    "execute_tool",
    "load_memory_summary",
    "refresh_session_instructions",
    "tools_for_personality",
]

logger = logging.getLogger(__name__)

TOOL_GROUPS: tuple[ToolGroup, ...] = (
    PERSONA_TOOL_GROUP,
    PERSONA_ADMIN_TOOL_GROUP,
    THEME_TOOL_GROUP,
    MEMORY_TOOL_GROUP,
    EPISODIC_TOOL_GROUP,
    SKILL_TOOL_GROUP,
    TIMER_TOOL_GROUP,
    VISION_TOOL_GROUP,
    CHANNEL_TOOL_GROUP,
)

ALL_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = flatten_tool_definitions(TOOL_GROUPS)


def _default_visible_groups() -> list[ToolGroup]:
    return [group for group in TOOL_GROUPS if group.default_visible]


def _groups_for_instructions(personality_id: str | None) -> list[ToolGroup]:
    if not personality_id:
        return _default_visible_groups()
    try:
        profile = get_personality(personality_id, validate_voice=False)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Could not resolve personality %r for tool groups: %s", personality_id, exc)
        return _default_visible_groups()
    try:
        return resolve_visible_groups(
            TOOL_GROUPS,
            profile,
            default_personality_id=DEFAULT_PERSONALITY_ID,
        )
    except ValueError as exc:
        logger.warning("Invalid tool_groups for %r: %s", personality_id, exc)
        return _default_visible_groups()


def _build_routing_section(groups: list[ToolGroup]) -> str:
    lines = [
        "## Tool routing",
        "Choose tools using this decision tree:",
    ]
    for group in groups:
        lines.append(f"- {group.title} (`{group.id}`): {group.when_to_use}")
    return "\n".join(lines)


def _build_active_context_section(personality_id: str) -> str | None:
    try:
        profile = get_personality(personality_id, validate_voice=False)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Could not build active context for %r: %s", personality_id, exc)
        return None
    return (
        "## Active context\n"
        f"Current personality: {profile.name} (id: {profile.id})."
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
    visible_groups = _groups_for_instructions(personality_id)
    parts = [
        base_prompt.strip(),
        _build_routing_section(visible_groups),
    ]

    if personality_id:
        active_context = _build_active_context_section(personality_id)
        if active_context:
            parts.append(active_context)

    for group in visible_groups:
        parts.append(f"## {group.title}\n{group.instructions}")

    parts.append(build_listening_pause_instructions())
    try:
        active_name = get_active_personality(validate_voice=False).name
    except (FileNotFoundError, ValueError):
        active_name = "Buddy"
    parts.append(build_barge_in_instructions(active_name))

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


def tools_for_personality(profile: PersonalityProfile) -> list[RealtimeFunctionTool]:
    """Return session tool defs visible for the given personality profile."""
    return visible_tool_definitions(
        TOOL_GROUPS,
        profile,
        default_personality_id=DEFAULT_PERSONALITY_ID,
    )


def execute_tool(
    memory_root: Path,
    tool_name: str,
    arguments_json: str,
    *,
    persona_namespace: str,
    turn_id: str | None = None,
    turn_revision: int | None = None,
) -> ToolExecutionResult:
    try:
        args: dict[str, Any] = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        return tool_error(tool_name, f"invalid tool arguments JSON: {exc}")

    try:
        if tool_name in VISION_TOOL_NAMES:
            return execute_vision_tool(tool_name, args)

        if tool_name in MEMORY_TOOL_NAMES:
            return execute_memory_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in EPISODIC_TOOL_NAMES:
            return execute_episodic_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in PERSONALITY_TOOL_NAMES:
            return execute_personality_tool(tool_name, args)

        if tool_name in THEME_TOOL_NAMES:
            return execute_theme_tool(tool_name, args)

        if tool_name in SKILL_TOOL_NAMES:
            return execute_skill_tool(memory_root, persona_namespace, tool_name, args)

        if tool_name in TIMER_TOOL_NAMES:
            return execute_timer_tool(tool_name, args)

        if tool_name in CHANNEL_TOOL_NAMES:
            return execute_channel_tool(
                tool_name,
                args,
                turn_id=turn_id,
                turn_revision=turn_revision,
            )

        return tool_error(tool_name, f"unknown tool {tool_name!r}")
    except ValueError as exc:
        return tool_error(tool_name, str(exc), context=safe_tool_context(args))
    except OSError as exc:
        log_tool_failure(tool_name, f"could not access memory file: {exc}", exc=exc, context=safe_tool_context(args))
        return ToolExecutionResult(output=f"Error: could not access memory file: {exc}")

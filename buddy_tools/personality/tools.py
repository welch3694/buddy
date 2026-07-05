"""Personality and voice tools exposed to the LLM during conversation."""

from __future__ import annotations

import json
from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.personality import (
    DEFAULT_PERSONALITY_ID,
    create_personality,
    delete_personality,
    get_active_personality,
    get_personality,
    list_personalities,
    update_personality,
)
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import safe_tool_context, tool_error
from buddy_tools.voice.voices import DEFAULT_VOICE_ID, list_voices

PERSONALITY_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="list_personalities",
        description="List available assistant personalities by id.",
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="list_voices",
        description="List available cloned voices by id.",
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="switch_personality",
        description=(
            "Switch to a different assistant personality. Use when the user asks to "
            "become someone else, switch personas, or talk to a named personality."
        ),
        parameters={
            "type": "object",
            "properties": {
                "personality_id": {
                    "type": "string",
                    "description": "Personality id to activate, e.g. buddy or coach",
                }
            },
            "required": ["personality_id"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="switch_voice",
        description=(
            "Change only the speaking voice without changing the full personality. "
            "Use when the user asks to sound like a different cloned voice."
        ),
        parameters={
            "type": "object",
            "properties": {
                "voice_id": {
                    "type": "string",
                    "description": "Voice id from list_voices, e.g. cliff",
                }
            },
            "required": ["voice_id"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="read_personality",
        description=(
            "Read the on-disk personality profile (prompt.md and profile.yaml fields only). "
            "Use before update_personality to inspect current persona content — not the "
            "assembled session instructions or memory snapshot."
        ),
        parameters={
            "type": "object",
            "properties": {
                "personality_id": {
                    "type": "string",
                    "description": "Personality id to read; defaults to the active personality",
                }
            },
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="create_personality",
        description=(
            "Create a new assistant personality after gathering name, role, tone, and "
            "optional voice_id from the user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "personality_id": {"type": "string"},
                "name": {"type": "string", "description": "Display name, e.g. Coach"},
                "prompt": {
                    "type": "string",
                    "description": (
                        "Persona-only content for prompt.md — identity, tone, role, and traits. "
                        "Never include tool instructions, memory snapshot, or system prompt boilerplate."
                    ),
                },
                "description": {"type": "string"},
                "voice_id": {
                    "type": "string",
                    "description": "Voice id from list_voices; defaults to cliff",
                },
            },
            "required": ["personality_id", "name", "prompt"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="update_personality",
        description=(
            "Update an existing personality's name, description, prompt, or voice_id. "
            "Use when the user wants to refine how a persona behaves or sounds. "
            "The prompt field must be persona-only content for prompt.md."
        ),
        parameters={
            "type": "object",
            "properties": {
                "personality_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "prompt": {"type": "string", "description": "Persona-only prompt.md content — identity, tone, role, traits. Not tool docs or system prompt stack."},
                "voice_id": {"type": "string"},
            },
            "required": ["personality_id"],
        },
    ),
    RealtimeFunctionTool(
        type="function",
        name="delete_personality",
        description=(
            "Delete a personality. Cannot delete the default buddy personality."
        ),
        parameters={
            "type": "object",
            "properties": {
                "personality_id": {"type": "string"},
            },
            "required": ["personality_id"],
        },
    ),
]

PERSONALITY_TOOL_NAMES = frozenset(tool.name for tool in PERSONALITY_TOOL_DEFINITIONS)


def build_personality_instructions() -> str:
    return (
        "You can manage assistant personalities and voices:\n"
        "- list_personalities / list_voices: see what is available\n"
        "- read_personality: load on-disk prompt.md and profile fields before editing\n"
        "- switch_personality: become a different persona when asked\n"
        "- switch_voice: change only the cloned voice\n"
        "- create_personality: make a new persona after asking what they should be like\n"
        "- update_personality: refine an existing persona (persona-only prompt.md content)\n"
        "- delete_personality: remove a persona (not buddy)\n"
        "After switching personalities, respond briefly in character without mentioning tools."
    )


def execute_personality_tool(tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
    if tool_name == "list_personalities":
        personalities = list_personalities()
        active = get_active_personality()
        return ToolExecutionResult(
            output=json.dumps({"personalities": personalities, "active": active.id})
        )

    if tool_name == "list_voices":
        return ToolExecutionResult(output=json.dumps({"voices": list_voices()}))

    if tool_name == "read_personality":
        raw_id = str(args.get("personality_id", "")).strip()
        try:
            profile = get_personality(raw_id) if raw_id else get_active_personality()
        except (FileNotFoundError, ValueError) as exc:
            return tool_error(tool_name, str(exc), context=safe_tool_context(args))
        payload = {
            "personality_id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "prompt": profile.prompt,
            "voice_id": profile.voice_id,
            "memory_namespace": profile.memory_namespace,
            "behaviors": profile.behaviors,
        }
        return ToolExecutionResult(output=json.dumps(payload))

    if tool_name == "switch_personality":
        personality_id = str(args.get("personality_id", "")).strip()
        if not personality_id:
            return tool_error(tool_name, "personality_id is empty", context=safe_tool_context(args))
        try:
            profile = get_personality(personality_id)
        except (FileNotFoundError, ValueError) as exc:
            return tool_error(tool_name, str(exc), context=safe_tool_context(args))
        return ToolExecutionResult(
            output=f"Now speaking as {profile.name}.",
            personality_switch_id=profile.id,
        )

    if tool_name == "switch_voice":
        voice_id = str(args.get("voice_id", "")).strip()
        if not voice_id:
            return tool_error(tool_name, "voice_id is empty", context=safe_tool_context(args))
        return ToolExecutionResult(
            output=f"Switching to the {voice_id} voice.",
            voice_switch_id=voice_id,
        )

    if tool_name == "create_personality":
        personality_id = str(args.get("personality_id", "")).strip()
        name = str(args.get("name", "")).strip()
        prompt = str(args.get("prompt", "")).strip()
        if not personality_id or not name or not prompt:
            return tool_error(
                tool_name,
                "personality_id, name, and prompt are required",
                context=safe_tool_context(args),
            )
        voice_id = str(args.get("voice_id", DEFAULT_VOICE_ID)).strip() or DEFAULT_VOICE_ID
        description = str(args.get("description", "")).strip()
        try:
            profile = create_personality(
                personality_id,
                name,
                prompt,
                description=description,
                voice_id=voice_id,
            )
        except (FileExistsError, FileNotFoundError, ValueError) as exc:
            return tool_error(tool_name, str(exc), context=safe_tool_context(args))
        return ToolExecutionResult(output=f"Created personality {profile.name}.")

    if tool_name == "update_personality":
        personality_id = str(args.get("personality_id", "")).strip()
        if not personality_id:
            return tool_error(tool_name, "personality_id is required", context=safe_tool_context(args))
        updates = {
            key: str(args[key]).strip()
            for key in ("name", "description", "prompt", "voice_id")
            if key in args and str(args[key]).strip()
        }
        if not updates:
            return tool_error(tool_name, "no fields to update", context=safe_tool_context(args))
        try:
            profile = update_personality(personality_id, **updates)
        except (FileNotFoundError, ValueError) as exc:
            return tool_error(tool_name, str(exc), context=safe_tool_context(args))
        return ToolExecutionResult(output=f"Updated personality {profile.name}.")

    if tool_name == "delete_personality":
        personality_id = str(args.get("personality_id", "")).strip()
        if not personality_id:
            return tool_error(tool_name, "personality_id is required", context=safe_tool_context(args))
        if personality_id == DEFAULT_PERSONALITY_ID:
            return tool_error(
                tool_name,
                f"cannot delete {DEFAULT_PERSONALITY_ID!r}",
                context=safe_tool_context(args),
            )
        try:
            delete_personality(personality_id)
        except (FileNotFoundError, ValueError) as exc:
            return tool_error(tool_name, str(exc), context=safe_tool_context(args))
        return ToolExecutionResult(output=f"Deleted personality {personality_id}.")

    return tool_error(tool_name, f"unknown personality tool {tool_name!r}")

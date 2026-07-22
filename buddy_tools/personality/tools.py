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
from buddy_tools.core.consolidate import ActionSpec, build_action_tool, resolve_action_args
from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import safe_tool_context, tool_error
from buddy_tools.voice.voices import DEFAULT_VOICE_ID, list_voices

_PERSONALITY_ID_PROPERTY = {
    "type": "string",
    "description": "Personality id, e.g. buddy or coach",
}

PERSONA_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(action="list", legacy_name="list_personalities"),
    ActionSpec(action="list_voices", legacy_name="list_voices"),
    ActionSpec(
        action="switch",
        legacy_name="switch_personality",
        required=("personality_id",),
        properties={
            "personality_id": {
                "type": "string",
                "description": "Personality id to activate, e.g. buddy or coach",
            }
        },
    ),
    ActionSpec(
        action="switch_voice",
        legacy_name="switch_voice",
        required=("voice_id",),
        properties={
            "voice_id": {
                "type": "string",
                "description": "Voice id from persona(action=list_voices), e.g. cliff",
            }
        },
    ),
    ActionSpec(
        action="read",
        legacy_name="read_personality",
        properties={
            "personality_id": {
                "type": "string",
                "description": "Personality id to read; defaults to the active personality",
            }
        },
    ),
    ActionSpec(
        action="update",
        legacy_name="update_personality",
        required=("personality_id",),
        properties={
            "personality_id": _PERSONALITY_ID_PROPERTY,
            "name": {"type": "string"},
            "description": {"type": "string"},
            "prompt": {
                "type": "string",
                "description": (
                    "Persona-only prompt.md content — identity, tone, role, traits. "
                    "Not tool docs or system prompt stack."
                ),
            },
            "voice_id": {"type": "string"},
        },
    ),
)

PERSONA_ADMIN_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        action="create",
        legacy_name="create_personality",
        required=("personality_id", "name", "prompt"),
        properties={
            "personality_id": _PERSONALITY_ID_PROPERTY,
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
                "description": "Voice id from persona(action=list_voices); defaults to cliff",
            },
        },
    ),
    ActionSpec(
        action="delete",
        legacy_name="delete_personality",
        required=("personality_id",),
        properties={"personality_id": _PERSONALITY_ID_PROPERTY},
    ),
)

PERSONA_TOOL_DEFINITION: RealtimeFunctionTool = build_action_tool(
    name="persona",
    description=(
        "Personality and voice operations. Use action=list/list_voices to see what is available, "
        "action=read to inspect the on-disk personality profile, action=switch/switch_voice to "
        "change persona or voice, or action=update to refine an existing persona."
    ),
    actions=PERSONA_ACTIONS,
)

PERSONA_ADMIN_TOOL_DEFINITION: RealtimeFunctionTool = build_action_tool(
    name="persona_admin",
    description=(
        "Personality admin operations. Use action=create to make a new personality, "
        "or action=delete to remove one (not the default buddy personality)."
    ),
    actions=PERSONA_ADMIN_ACTIONS,
)

PERSONALITY_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    PERSONA_TOOL_DEFINITION,
    PERSONA_ADMIN_TOOL_DEFINITION,
]
PERSONALITY_TOOL_NAMES = frozenset({"persona", "persona_admin"})


def build_persona_instructions() -> str:
    return (
        "Identity rule: You are only the active personality. When the user asks for a "
        "different persona, to become someone else, or to talk to a named personality, "
        "you MUST call persona(action=switch). Never impersonate another persona without that tool.\n"
        "You can switch, inspect, and refine personalities and voices with the persona tool:\n"
        "- persona(action=list) / persona(action=list_voices): see what is available\n"
        "- persona(action=read): load on-disk prompt.md and profile fields before editing\n"
        "- persona(action=switch, personality_id=...): become a different persona when asked\n"
        "- persona(action=switch_voice, voice_id=...): change only the cloned voice\n"
        "- persona(action=update, personality_id=...): refine how a persona behaves or sounds "
        "(persona-only prompt.md content)\n"
        "After switching personalities, respond briefly in character without mentioning tools."
    )


def build_persona_admin_instructions() -> str:
    return (
        "You can create and delete assistant personalities with the persona_admin tool:\n"
        "- persona_admin(action=create): make a new persona after asking what they should be like\n"
        "- persona_admin(action=delete): remove a persona (not buddy)\n"
        "Prompt content for persona_admin(action=create) must be persona-only — never include tool "
        "instructions, memory snapshots, or system prompt boilerplate."
    )


def build_personality_instructions() -> str:
    """Combined personality help (persona + admin) for tests and legacy callers."""
    return f"{build_persona_instructions()}\n{build_persona_admin_instructions()}"


PERSONA_TOOL_GROUP = ToolGroup(
    id="persona",
    title="Persona",
    when_to_use=(
        "User asks to switch personas, change voice, list or read personalities, "
        "or refine the active persona with persona(action=update). Prefer switch over roleplay."
    ),
    tools=(PERSONA_TOOL_DEFINITION,),
    instructions=build_persona_instructions(),
)

PERSONA_ADMIN_TOOL_GROUP = ToolGroup(
    id="persona_admin",
    title="Persona admin",
    when_to_use=(
        "User asks to create or delete a personality (admin; not for roleplay or edits)."
    ),
    tools=(PERSONA_ADMIN_TOOL_DEFINITION,),
    instructions=build_persona_admin_instructions(),
    default_visible=False,
    admin_only=True,
)


def execute_personality_tool(tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
    if tool_name in ("persona", "persona_admin"):
        actions = PERSONA_ACTIONS if tool_name == "persona" else PERSONA_ADMIN_ACTIONS
        resolved = resolve_action_args(tool_name, args, actions)
        if isinstance(resolved, ToolExecutionResult):
            return resolved
        tool_name, args = resolved

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
            "tool_groups": list(profile.tool_groups),
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

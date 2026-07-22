"""Theme list/switch tools exposed to the LLM (#138, consolidated #109)."""

from __future__ import annotations

import json
from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.consolidate import ActionSpec, build_action_tool, resolve_action_args
from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import safe_tool_context, tool_error
from buddy_tools.themes.catalog import get_active_theme_id, get_theme, list_themes
from buddy_tools.themes.schema import ThemeValidationError

THEME_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(action="list", legacy_name="list_themes"),
    ActionSpec(
        action="switch",
        legacy_name="switch_theme",
        required=("theme_id",),
        properties={
            "theme_id": {
                "type": "string",
                "description": "Theme id from theme(action=list), e.g. default or ember",
            }
        },
    ),
)

THEME_TOOL_DEFINITION: RealtimeFunctionTool = build_action_tool(
    name="theme",
    description=(
        "Companion display theme operations. Use action=list to see installed themes, "
        "or action=switch with theme_id to change the look, colors, or visual theme."
    ),
    actions=THEME_ACTIONS,
)

THEME_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [THEME_TOOL_DEFINITION]
THEME_TOOL_NAMES = frozenset({"theme"})


def build_theme_instructions() -> str:
    return (
        "You can change the companion display theme (colors and orb mood) with the theme tool:\n"
        "- theme(action=list): see installed themes and which is active\n"
        "- theme(action=switch, theme_id=...): apply a theme immediately without restart\n"
        "After switching, confirm briefly without mentioning tools."
    )


THEME_TOOL_GROUP = ToolGroup(
    id="theme",
    title="Theme",
    when_to_use=(
        "User asks to change the companion look, colors, display theme, or visual style."
    ),
    tools=(THEME_TOOL_DEFINITION,),
    instructions=build_theme_instructions(),
)


def execute_theme_tool(tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
    if tool_name == "theme":
        resolved = resolve_action_args("theme", args, THEME_ACTIONS)
        if isinstance(resolved, ToolExecutionResult):
            return resolved
        tool_name, args = resolved

    if tool_name == "list_themes":
        themes = list_themes()
        active = get_active_theme_id()
        return ToolExecutionResult(
            output=json.dumps({"themes": themes, "active": active})
        )

    if tool_name == "switch_theme":
        theme_id = str(args.get("theme_id", "")).strip()
        if not theme_id:
            return tool_error(
                tool_name,
                "theme_id is required",
                context=safe_tool_context(args),
            )
        try:
            pack = get_theme(theme_id)
        except (FileNotFoundError, ThemeValidationError) as exc:
            return tool_error(tool_name, str(exc), context=safe_tool_context(args))
        return ToolExecutionResult(
            output=f"Switching to the {pack.name} theme.",
            theme_switch_id=pack.id,
        )

    return tool_error(tool_name, f"unknown theme tool {tool_name!r}")

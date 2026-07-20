"""Theme list/switch tools exposed to the LLM (#138)."""

from __future__ import annotations

import json
from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.groups import ToolGroup
from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import safe_tool_context, tool_error
from buddy_tools.themes.catalog import get_active_theme_id, get_theme, list_themes
from buddy_tools.themes.schema import ThemeValidationError

_THEME_TOOL_DEFINITIONS: list[RealtimeFunctionTool] = [
    RealtimeFunctionTool(
        type="function",
        name="list_themes",
        description="List installed companion display themes by id and name.",
        parameters={"type": "object", "properties": {}},
    ),
    RealtimeFunctionTool(
        type="function",
        name="switch_theme",
        description=(
            "Switch the companion display theme. Use when the user asks to change "
            "the look, colors, or visual theme of the agent panel."
        ),
        parameters={
            "type": "object",
            "properties": {
                "theme_id": {
                    "type": "string",
                    "description": "Theme id from list_themes, e.g. default or ember",
                }
            },
            "required": ["theme_id"],
        },
    ),
]

THEME_TOOL_DEFINITIONS = list(_THEME_TOOL_DEFINITIONS)
THEME_TOOL_NAMES = frozenset(tool.name for tool in THEME_TOOL_DEFINITIONS)


def build_theme_instructions() -> str:
    return (
        "You can change the companion display theme (colors and orb mood):\n"
        "- list_themes: see installed themes and which is active\n"
        "- switch_theme: apply a theme immediately without restart\n"
        "After switching, confirm briefly without mentioning tools."
    )


THEME_TOOL_GROUP = ToolGroup(
    id="theme",
    title="Theme",
    when_to_use=(
        "User asks to change the companion look, colors, display theme, or visual style."
    ),
    tools=tuple(_THEME_TOOL_DEFINITIONS),
    instructions=build_theme_instructions(),
)


def execute_theme_tool(tool_name: str, args: dict[str, Any]) -> ToolExecutionResult:
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

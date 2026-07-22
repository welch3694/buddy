"""Build action-parameter consolidated tools and resolve action → legacy dispatch (#109)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from openai.types.realtime import RealtimeFunctionTool

from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import safe_tool_context, tool_error


@dataclass(frozen=True)
class ActionSpec:
    """One action on a consolidated tool and its legacy internal tool name."""

    action: str
    legacy_name: str
    required: tuple[str, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)


def build_action_tool(
    *,
    name: str,
    description: str,
    actions: Sequence[ActionSpec],
) -> RealtimeFunctionTool:
    """Build a single RealtimeFunctionTool with required ``action`` enum + unioned props.

    Only ``action`` is schema-required. Per-action required fields are enforced by
    :func:`resolve_action_args` so JSON Schema does not need conflicting ``oneOf`` lists.
    """
    if not actions:
        raise ValueError(f"actions required for consolidated tool {name!r}")

    enum_values = [spec.action for spec in actions]
    properties: dict[str, Any] = {
        "action": {
            "type": "string",
            "enum": enum_values,
            "description": "Which operation to perform. Available: " + ", ".join(enum_values),
        }
    }
    for spec in actions:
        for key, prop in spec.properties.items():
            if key == "action":
                raise ValueError(f"property name 'action' is reserved on tool {name!r}")
            if key not in properties:
                properties[key] = prop

    return RealtimeFunctionTool(
        type="function",
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": ["action"],
        },
    )


def _is_missing_required(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def resolve_action_args(
    tool_name: str,
    args: dict[str, Any],
    actions: Sequence[ActionSpec],
) -> tuple[str, dict[str, Any]] | ToolExecutionResult:
    """Map consolidated args to ``(legacy_tool_name, remaining_args)`` or an error result."""
    by_action = {spec.action: spec for spec in actions}
    raw = args.get("action")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return tool_error(tool_name, "action is required", context=safe_tool_context(args))

    action = str(raw).strip()
    spec = by_action.get(action)
    if spec is None:
        allowed = ", ".join(sorted(by_action))
        return tool_error(
            tool_name,
            f"unknown action {action!r}; expected one of: {allowed}",
            context=safe_tool_context(args),
        )

    remaining = {key: value for key, value in args.items() if key != "action"}
    missing = [
        key
        for key in spec.required
        if key not in remaining or _is_missing_required(remaining[key])
    ]
    if missing:
        return tool_error(
            tool_name,
            f"action {action!r} requires: {', '.join(missing)}",
            context=safe_tool_context(args),
        )
    return spec.legacy_name, remaining


def action_legacy_map(actions: Sequence[ActionSpec]) -> dict[str, str]:
    """Return ``{action: legacy_name}`` for docs/tests."""
    return {spec.action: spec.legacy_name for spec in actions}

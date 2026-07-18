"""Tool group registration and per-persona visibility."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from openai.types.realtime import RealtimeFunctionTool


class HasToolGroupVisibility(Protocol):
    id: str
    tool_groups: tuple[str, ...]


@dataclass(frozen=True)
class ToolGroup:
    id: str
    title: str
    when_to_use: str
    tools: tuple[RealtimeFunctionTool, ...]
    instructions: str
    default_visible: bool = True
    admin_only: bool = False


def tool_names(group: ToolGroup) -> frozenset[str]:
    return frozenset(tool.name for tool in group.tools)


def flatten_tool_definitions(groups: Sequence[ToolGroup]) -> list[RealtimeFunctionTool]:
    seen: set[str] = set()
    tools: list[RealtimeFunctionTool] = []
    for group in groups:
        for tool in group.tools:
            if tool.name in seen:
                raise ValueError(f"Duplicate tool name {tool.name!r} across tool groups")
            seen.add(tool.name)
            tools.append(tool)
    return tools


def resolve_visible_groups(
    groups: Sequence[ToolGroup],
    profile: HasToolGroupVisibility,
    *,
    default_personality_id: str = "buddy",
) -> list[ToolGroup]:
    """Return groups visible for the active personality.

    A group is visible when any of:
    - ``default_visible`` is True
    - its id is listed in ``profile.tool_groups``
    - ``admin_only`` is True and the profile is the default personality (buddy)
    """
    known = {group.id for group in groups}
    unknown = [gid for gid in profile.tool_groups if gid not in known]
    if unknown:
        raise ValueError(
            f"Personality {profile.id!r} has unknown tool_groups: {', '.join(sorted(unknown))}"
        )

    opted_in = set(profile.tool_groups)
    visible: list[ToolGroup] = []
    for group in groups:
        if group.default_visible or group.id in opted_in:
            visible.append(group)
            continue
        if group.admin_only and profile.id == default_personality_id:
            visible.append(group)
    return visible


def visible_tool_definitions(
    groups: Sequence[ToolGroup],
    profile: HasToolGroupVisibility,
    *,
    default_personality_id: str = "buddy",
) -> list[RealtimeFunctionTool]:
    return flatten_tool_definitions(
        resolve_visible_groups(groups, profile, default_personality_id=default_personality_id)
    )

"""Shared types for local tool execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolExecutionResult:
    output: str
    image_data_uri: str | None = None
    image_caption: str | None = None
    personality_switch_id: str | None = None
    voice_switch_id: str | None = None
    refresh_instructions: bool = False
    include_full_skill_body: bool = False

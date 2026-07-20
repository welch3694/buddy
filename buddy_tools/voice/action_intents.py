"""Deterministic phrase → tool intent router for high-confidence voice actions (#145)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from buddy_tools.voice.listening_pause import normalize_transcript

# Prefix phrases matched in priority order (first hit wins).
_CANCEL_PREFIXES = (
    "cancel skill",
    "stop director",
    "stop the director",
)
_PAUSE_PREFIXES = (
    "pause skill",
    "pause director",
    "pause the director",
)
_LIVE_DIRECTOR_PREFIXES = (
    "start live director",
    "start director",
    "go live",
    "director flow",
)
_REMEMBER_PREFIXES = (
    "remember that",
    "dont forget",
    "keep in mind",
)
_EDIT_PERSONALITY_PREFIXES = (
    "edit personality",
    "change how you talk",
    "change your personality",
)

_SWITCH_PERSONALITY_RE = re.compile(
    r"^(?:become|switch to)\s+(.+)$",
)


@dataclass(frozen=True)
class ActionIntent:
    """Matched voice action: tool to force plus suggested arguments (for tests/docs)."""

    tool_name: str
    arguments: dict[str, Any]


def _sanitize_personality_id(raw: str) -> str | None:
    """Sanitize a spoken personality token; return None if empty/invalid."""
    cleaned = raw.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    return cleaned or None


def _matches_prefix(normalized: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if normalized == prefix or normalized.startswith(prefix + " "):
            return True
    return False


def match_action_intent(text: str) -> ActionIntent | None:
    """Return a forced-tool intent for high-confidence voice phrases, else None."""
    normalized = normalize_transcript(text)
    if not normalized:
        return None

    if _matches_prefix(normalized, _CANCEL_PREFIXES):
        return ActionIntent(tool_name="cancel_skill", arguments={})

    if _matches_prefix(normalized, _PAUSE_PREFIXES):
        return ActionIntent(tool_name="pause_skill", arguments={})

    if _matches_prefix(normalized, _LIVE_DIRECTOR_PREFIXES):
        return ActionIntent(tool_name="start_skill", arguments={"name": "live-director"})

    if _matches_prefix(normalized, _REMEMBER_PREFIXES):
        return ActionIntent(tool_name="start_skill", arguments={"name": "remember"})

    if _matches_prefix(normalized, _EDIT_PERSONALITY_PREFIXES):
        return ActionIntent(tool_name="start_skill", arguments={"name": "edit-personality"})

    switch_match = _SWITCH_PERSONALITY_RE.match(normalized)
    if switch_match is not None:
        personality_id = _sanitize_personality_id(switch_match.group(1))
        if personality_id is not None:
            return ActionIntent(
                tool_name="switch_personality",
                arguments={"personality_id": personality_id},
            )

    return None

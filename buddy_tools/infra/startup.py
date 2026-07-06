"""Startup configuration: active personality prompt and voice clone paths."""

from __future__ import annotations

import sys
from typing import Any

from buddy_tools.personality import get_active_personality
from buddy_tools.voice.voices import resolve_voice

FIXED_VOICE_INSTRUCTIONS = """\
Reply directly in natural spoken language only.
Never explain your reasoning, planning, or what the user asked for.
Be warm and conversational, not formal or robotic.
Keep answers concise unless the user asks for more detail.
Do not mention tools, files, memory, or how you work unless the user explicitly asks.\
"""


def build_voice_system_prompt(personality_prompt: str) -> str:
    """Combine a personality prompt with global fixed voice rules."""
    prompt = personality_prompt.strip()
    rules = FIXED_VOICE_INSTRUCTIONS.strip()
    if not prompt:
        raise ValueError("personality prompt cannot be empty")
    return f"{prompt}\n\n{rules}"


def build_init_instructions() -> str:
    """Build init chat prompt from the active personality plus fixed voice rules."""
    profile = get_active_personality()
    return build_voice_system_prompt(profile.prompt)


def inject_s2s_init_chat_prompt(argv: list[str] | None = None) -> list[str]:
    """Return argv with ``--init_chat_prompt`` set from the active personality.

    Strips any existing ``--init_chat_prompt`` so prompts with embedded quotes
    (e.g. ``"call him out"``) are not broken by Windows command-line parsing.
    """
    source = sys.argv if argv is None else argv
    script = source[:1]
    rest = source[1:]
    filtered: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--init_chat_prompt":
            i += 2
            continue
        if arg.startswith("--init_chat_prompt="):
            i += 1
            continue
        filtered.append(arg)
        i += 1
    return [*script, *filtered, "--init_chat_prompt", build_init_instructions()]


def resolve_startup_config() -> dict[str, Any]:
    """Return startup settings for the active personality and its voice."""
    from buddy_tools.infra.data_dir import configure_user_data

    data_dir = configure_user_data()
    profile = get_active_personality()
    audio_path, ref_text = resolve_voice(profile.voice_id)
    return {
        "personality_id": profile.id,
        "personality_name": profile.name,
        "voice_id": profile.voice_id,
        "init_chat_prompt": build_voice_system_prompt(profile.prompt),
        "audio": str(audio_path),
        "ref_text": ref_text,
        "data_dir": str(data_dir),
    }

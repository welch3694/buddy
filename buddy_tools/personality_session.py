"""Apply active personality changes to the live voice session."""

from __future__ import annotations

import logging
from pathlib import Path

from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.memory import load_memory_summary
from buddy_tools.personality import PersonalityProfile, get_personality, set_active_personality
from buddy_tools.registry import build_tool_instructions
from buddy_tools.startup import build_voice_system_prompt
from buddy_tools.voice_session import apply_voice

logger = logging.getLogger(__name__)


def reset_chat_history(chat: Chat) -> None:
    """Clear conversation history after a personality switch."""
    with chat._lock:
        chat.buffer.clear()
        chat._pending_tool_calls.clear()
        chat._user_turn_count = 0
    logger.info("Cleared chat history for personality switch")


def apply_personality_switch(
    personality_id: str,
    *,
    runtime_config: RuntimeConfig,
    chat: Chat,
    memory_root: Path,
) -> PersonalityProfile:
    """Activate a personality, refresh session instructions/voice, and reset chat."""
    set_active_personality(personality_id)
    profile = get_personality(personality_id)

    memory_root.mkdir(parents=True, exist_ok=True)
    summary = load_memory_summary(memory_root, profile.memory_namespace)
    base = build_voice_system_prompt(profile.prompt)
    runtime_config.session.instructions = build_tool_instructions(base, summary)

    apply_voice(profile.voice_id, runtime_config=runtime_config)
    reset_chat_history(chat)

    logger.info(
        "Switched to personality %r (%s) with voice %r",
        profile.id,
        profile.name,
        profile.voice_id,
    )
    return profile

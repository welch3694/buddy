"""Apply active personality changes to the live voice session."""

from __future__ import annotations

import logging
from pathlib import Path

from speech_to_speech.LLM.chat import Chat
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.memory import load_memory_summary
from buddy_tools.personality import PersonalityProfile, get_personality, set_active_personality
from buddy_tools.core.registry import build_tool_instructions, tools_for_personality
from buddy_tools.infra.startup import build_voice_system_prompt
from buddy_tools.voice.session import apply_voice

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
    """Activate a personality, refresh session instructions/voice/tools, and reset chat."""
    set_active_personality(personality_id)
    profile = get_personality(personality_id)

    memory_root.mkdir(parents=True, exist_ok=True)
    summary = load_memory_summary(memory_root, profile.memory_namespace)
    base = build_voice_system_prompt(profile.prompt)
    runtime_config.session.instructions = build_tool_instructions(
        base,
        summary,
        memory_root=memory_root,
        persona_namespace=profile.memory_namespace,
        personality_id=profile.id,
    )
    runtime_config.session.tools = tools_for_personality(profile)

    apply_voice(profile.voice_id, runtime_config=runtime_config)
    reset_chat_history(chat)

    from buddy_tools.companion.bridge import get_companion_bridge
    from buddy_tools.companion.publisher import emit_persona

    bridge = get_companion_bridge()
    if bridge is not None:
        bridge.set_active_persona(
            personality_id=profile.id,
            persona_name=profile.name,
            persona_namespace=profile.memory_namespace,
            voice_id=profile.voice_id,
        )
    else:
        emit_persona(
            personality_id=profile.id,
            name=profile.name,
            memory_namespace=profile.memory_namespace,
            voice_id=profile.voice_id,
        )

    logger.info(
        "Switched to personality %r (%s) with voice %r",
        profile.id,
        profile.name,
        profile.voice_id,
    )
    return profile

"""Keyword barge-in via \"hey {persona name}\" to interrupt Buddy mid-speech."""

from __future__ import annotations

import logging
import re
import threading

from buddy_tools.voice.listening_pause import (
    get_listening_pause_controller,
    normalize_transcript,
)

logger = logging.getLogger(__name__)

_WAKE_LEAD = "hey"
_barge_in_active = False
_barge_in_lock = threading.Lock()


def build_wake_prefix(persona_name: str) -> str:
    """Return normalized wake phrase for a persona display name (e.g. ``hey coach``)."""
    name = normalize_transcript(persona_name)
    if not name:
        return _WAKE_LEAD
    return f"{_WAKE_LEAD} {name}"


def _wake_prefixes(persona_name: str, persona_id: str | None = None) -> tuple[str, ...]:
    prefixes: list[str] = []
    for label in (persona_name, persona_id):
        if not label or not str(label).strip():
            continue
        prefix = build_wake_prefix(str(label))
        if prefix not in prefixes and prefix != _WAKE_LEAD:
            prefixes.append(prefix)
    return tuple(prefixes)


def _strip_wake_from_original(transcript: str, prefix: str) -> str:
    """Remove the wake prefix from the original transcript, preserving remainder casing."""
    words = prefix.split()
    if not words:
        return transcript.strip()
    joined = r"(?:[\s,.:;!?'\"\-]+)".join(re.escape(w) for w in words)
    pattern = rf"^\s*{joined}[\s,.:;!?'\"\-]*"
    match = re.match(pattern, transcript, flags=re.IGNORECASE)
    if match is not None:
        return transcript[match.end() :].strip()
    # Fallback: normalized remainder after confirmed prefix match.
    normalized = normalize_transcript(transcript)
    if normalized == prefix:
        return ""
    if normalized.startswith(prefix + " "):
        return normalized[len(prefix) + 1 :].strip()
    return ""


def match_barge_in_prefix(
    transcript: str,
    persona_name: str,
    persona_id: str | None = None,
) -> str | None:
    """Return remainder after a prefix-anchored wake phrase, or ``None`` if no match.

    Empty string means the wake phrase matched with no follow-up request yet.
    """
    normalized = normalize_transcript(transcript)
    if not normalized:
        return None
    for prefix in _wake_prefixes(persona_name, persona_id):
        if normalized == prefix or normalized.startswith(prefix + " "):
            return _strip_wake_from_original(transcript, prefix)
    return None


def match_active_barge_in(transcript: str) -> str | None:
    """Match barge-in against the currently active personality name and id."""
    from buddy_tools.personality import get_active_personality

    try:
        profile = get_active_personality(validate_voice=False)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Barge-in skipped; could not load active personality: %s", exc)
        return None
    return match_barge_in_prefix(transcript, profile.name, profile.id)


def set_barge_in_active(active: bool = True) -> None:
    """Mark the next voice commit as a barge-in turn (silence-gate bypass)."""
    global _barge_in_active
    with _barge_in_lock:
        _barge_in_active = bool(active)


def consume_barge_in_active() -> bool:
    """Return and clear the barge-in flag (one-shot for the committing turn)."""
    global _barge_in_active
    with _barge_in_lock:
        active = _barge_in_active
        _barge_in_active = False
        return active


def is_barge_in_active() -> bool:
    """True when a barge-in turn is pending (tests / introspection)."""
    with _barge_in_lock:
        return _barge_in_active


def reset_barge_in_for_tests() -> None:
    """Clear barge-in state between tests."""
    global _barge_in_active
    with _barge_in_lock:
        _barge_in_active = False


def interrupt_for_barge_in() -> None:
    """Cancel in-flight LLM/TTS, drain local audio, and abort an active pulse turn."""
    controller = get_listening_pause_controller()
    if controller.cancel_scope is not None:
        try:
            controller.cancel_scope.cancel()
        except Exception:
            logger.exception("Failed to cancel pipeline on barge-in")

    from buddy_tools.pulse.inject import (
        abort_in_flight_pulse_for_user_speech,
        drain_local_audio_output,
    )

    drain_local_audio_output()
    abort_in_flight_pulse_for_user_speech()
    logger.info("Barge-in interrupt: cancelled in-flight response and drained audio")


def build_barge_in_instructions(persona_name: str) -> str:
    """Session instructions so the agent can explain the wake phrase."""
    display = (persona_name or "Buddy").strip() or "Buddy"
    wake = f"hey {display}"
    return (
        "Voice barge-in: The user can interrupt you mid-speech by saying "
        f'"{wake}" followed by their request (for example, "{wake}, I need a water break"). '
        "When they use this wake phrase, stop speaking immediately and treat the remainder "
        "as their turn. When the user asks how to interrupt you or get your attention while "
        f'you are talking, explain that they can say "{wake}".'
    )

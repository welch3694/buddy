"""Pulse turn gating — silence, busy deferral, mute, and interval checks."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Callable

from buddy_tools.voice.listening_pause import get_listening_pause_controller
from buddy_tools.pulse.schema import SessionConfig
from buddy_tools.pulse.state import PulseState

logger = logging.getLogger(__name__)

DEFAULT_MANDATORY_SILENCE_S = 1.5
DEFAULT_MANDATORY_MAX_DEFER_S = 30.0
DEFAULT_CONVERSATION_CHECK_S = 60.0
DEFAULT_MIN_SPEAK_INTERVAL_S = 45.0

_perf_counter_fn: Callable[[], float] = time.perf_counter
_last_user_speech_stopped_at_s: float | None = None


def set_perf_counter_for_tests(fn: Callable[[], float] | None) -> None:
    global _perf_counter_fn
    _perf_counter_fn = fn or time.perf_counter


def _perf_counter() -> float:
    return _perf_counter_fn()


def set_last_user_speech_stopped_at(speech_stopped_at_s: float | None) -> None:
    global _last_user_speech_stopped_at_s
    _last_user_speech_stopped_at_s = speech_stopped_at_s


def get_last_user_speech_stopped_at() -> float | None:
    return _last_user_speech_stopped_at_s


def reset_pulse_gates_for_tests() -> None:
    global _last_user_speech_stopped_at_s
    _last_user_speech_stopped_at_s = None
    set_perf_counter_for_tests(None)


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _seconds_since_iso(value: str | None, *, now: datetime) -> float | None:
    anchor = _parse_iso_timestamp(value)
    if anchor is None:
        return None
    return max(0.0, (now - anchor).total_seconds())


def _mandatory_silence_s(session: SessionConfig) -> float:
    return DEFAULT_MANDATORY_SILENCE_S


def _mandatory_max_defer_s(session: SessionConfig) -> float:
    configured = session.pulse.mandatory_cue_max_defer_s
    return configured if configured is not None else DEFAULT_MANDATORY_MAX_DEFER_S


def _conversation_check_s(session: SessionConfig) -> float:
    configured = session.pulse.conversation_check_s
    return configured if configured is not None else DEFAULT_CONVERSATION_CHECK_S


def _min_speak_interval_s(session: SessionConfig) -> float:
    configured = session.pulse.min_speak_interval_s
    return configured if configured is not None else DEFAULT_MIN_SPEAK_INTERVAL_S


def _pipeline_busy(*, should_listen) -> bool:
    if get_listening_pause_controller().paused:
        return True
    if should_listen is not None and not should_listen.is_set():
        return True
    return False


def _user_silent_for(*, silence_s: float) -> bool:
    last_speech = _last_user_speech_stopped_at_s
    if last_speech is None:
        return True
    return (_perf_counter() - last_speech) >= silence_s


def directed_pulse_gates_allow(
    state: PulseState,
    session: SessionConfig,
    *,
    should_listen,
) -> bool:
    if not state.pending_cue or not state.pending_cue.strip():
        return False
    if state.cue_priority != "mandatory" and state.pulse_mode != "directed":
        return False
    if state.narrator_muted:
        logger.info("Directed pulse deferred: narrator_muted for skill=%r", state.skill_name)
        return False

    now = datetime.now(UTC)
    pending_age = _seconds_since_iso(state.pending_cue_since, now=now)
    max_defer = _mandatory_max_defer_s(session)
    force_fire = pending_age is not None and pending_age >= max_defer

    if force_fire:
        logger.info(
            "Directed pulse force-fire after max defer (%.1fs) for skill=%r",
            max_defer,
            state.skill_name,
        )
        return True

    if _pipeline_busy(should_listen=should_listen):
        return False

    silence_s = _mandatory_silence_s(session)
    if not _user_silent_for(silence_s=silence_s):
        return False

    return True


def conversational_pulse_gates_allow(
    state: PulseState,
    session: SessionConfig,
    *,
    should_listen,
) -> bool:
    if state.pending_cue and state.cue_priority == "mandatory":
        return False
    if state.narrator_muted:
        return False
    if _pipeline_busy(should_listen=should_listen):
        return False

    now = datetime.now(UTC)
    min_interval = _min_speak_interval_s(session)
    since_assistant = _seconds_since_iso(state.last_assistant_speech_at, now=now)
    if since_assistant is not None and since_assistant < min_interval:
        return False

    conversation_check = _conversation_check_s(session)
    since_user = _seconds_since_iso(state.last_user_speech_at, now=now)
    if since_user is not None and since_user < conversation_check:
        return False

    last_conv = state.vars.get("last_conversation_pulse_at")
    since_conv = _seconds_since_iso(str(last_conv) if last_conv else None, now=now)
    if since_conv is not None and since_conv < conversation_check:
        return False

    if not _user_silent_for(silence_s=_mandatory_silence_s(session)):
        return False

    return True


def select_pulse_mode(
    state: PulseState,
    session: SessionConfig,
    *,
    should_listen,
) -> str | None:
    """Return 'directed', 'conversational', or None if no pulse should fire."""
    if state.pulse_in_flight:
        return None

    if state.pending_cue and state.cue_priority == "mandatory":
        if directed_pulse_gates_allow(state, session, should_listen=should_listen):
            return "directed"
        return None

    if conversational_pulse_gates_allow(state, session, should_listen=should_listen):
        return "conversational"

    return None

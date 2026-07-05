"""Pulse turn injection, completion hooks, and speech timestamp tracking."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Event, Lock
from typing import Any, Literal

from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams
from speech_to_speech.LLM.chat import make_user_message
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import GenerateResponseRequest, LLMResponseChunk

from buddy_tools.personality import get_active_personality
from buddy_tools.pulse.gates import select_pulse_mode, set_last_user_speech_stopped_at
from buddy_tools.pulse.schema import SessionConfig
from buddy_tools.pulse.state import PulseState, load_pulse_state, save_pulse_state

logger = logging.getLogger(__name__)

PULSE_NUDGE_PREFIX = "[Pulse — internal scheduled nudge, not user speech]: "
NO_OUTPUT_MARKER = "[NO_OUTPUT]"

PulseTurnMode = Literal["directed", "conversational"]


@dataclass
class ActivePulseTurn:
    memory_root: Path
    persona_namespace: str
    mode: PulseTurnMode
    clear_pending_cue: bool


_lock = Lock()
_active_pulse_turn: ActivePulseTurn | None = None
_pulse_turn_text: list[str] = []


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _state_snapshot(state: PulseState) -> dict[str, Any]:
    return {
        "skill_name": state.skill_name,
        "phase": state.phase,
        "pending_cue": state.pending_cue,
        "cue_priority": state.cue_priority,
        "pulse_mode": state.pulse_mode,
        "narrator_muted": state.narrator_muted,
        "vars": state.vars,
        "fired_rules": state.fired_rules,
        "last_user_speech_at": state.last_user_speech_at,
        "last_assistant_speech_at": state.last_assistant_speech_at,
    }


def build_directed_pulse_instructions(state: PulseState, base_instructions: str) -> str:
    snapshot = json.dumps(_state_snapshot(state), indent=2, sort_keys=True)
    return (
        f"{base_instructions}\n\n"
        "Pulse directed turn — deliver the pending cue naturally in spoken language.\n"
        f"Pending cue: {state.pending_cue}\n"
        "Do not call tools on this turn. Do not invent cues beyond the pending cue.\n"
        f"Pulse state snapshot:\n{snapshot}"
    )


def build_conversational_pulse_instructions(state: PulseState, base_instructions: str) -> str:
    snapshot = json.dumps(_state_snapshot(state), indent=2, sort_keys=True)
    return (
        f"{base_instructions}\n\n"
        "Pulse conversational turn — optionally speak briefly to maintain engagement during a lull.\n"
        f"If silence is appropriate, output exactly {NO_OUTPUT_MARKER} and nothing else.\n"
        "Do not call tools. Do not invent scheduled cues, camera switches, or mandatory directives.\n"
        f"Pulse state snapshot:\n{snapshot}"
    )


def _memory_root() -> Path:
    from buddy_tools.infra.bootstrap import get_memory_root

    return get_memory_root()


def record_user_speech(speech_stopped_at_s: float | None) -> None:
    """Update pulse state and gate timing when the user finishes speaking."""
    set_last_user_speech_stopped_at(speech_stopped_at_s)
    if speech_stopped_at_s is None:
        return

    try:
        profile = get_active_personality()
        memory_root = _memory_root()
        state = load_pulse_state(memory_root, profile.memory_namespace)
        if state is None or state.status != "active":
            return
        state.last_user_speech_at = _utc_now_iso()
        save_pulse_state(memory_root, profile.memory_namespace, state)
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("Could not record user speech on pulse state: %s", exc)


def _complete_pulse_turn(
    *,
    memory_root: Path,
    persona_namespace: str,
    mode: PulseTurnMode,
    clear_pending_cue: bool,
    spoke: bool,
    full_text: str,
) -> None:
    state = load_pulse_state(memory_root, persona_namespace)
    if state is None:
        return

    state.pulse_in_flight = False
    if spoke:
        state.last_assistant_speech_at = _utc_now_iso()
    if clear_pending_cue:
        state.pending_cue = None
        state.cue_priority = None
        state.pending_cue_since = None
        state.pulse_mode = "directed"
    if mode == "conversational":
        state.vars["last_conversation_pulse_at"] = _utc_now_iso()

    save_pulse_state(memory_root, persona_namespace, state)
    logger.info(
        "Pulse turn complete mode=%r skill=%r spoke=%s clear_pending=%s text=%r",
        mode,
        state.skill_name,
        spoke,
        clear_pending_cue,
        full_text[:80],
    )


def inject_pulse_turn(
    *,
    memory_root: Path,
    persona_namespace: str,
    state: PulseState,
    mode: PulseTurnMode,
    text_prompt_queue: Queue[Any],
    runtime_config: RuntimeConfig,
) -> bool:
    global _active_pulse_turn, _pulse_turn_text

    nudge = (
        f"Directed pulse: deliver pending cue — {state.pending_cue}"
        if mode == "directed"
        else "Conversational pulse: speak briefly or output [NO_OUTPUT]."
    )

    try:
        runtime_config.chat.add_item(make_user_message(f"{PULSE_NUDGE_PREFIX}{nudge}"))
    except Exception:
        logger.exception("Pulse turn failed to add nudge message to chat")
        return False

    base_instructions = runtime_config.session.instructions or ""
    if mode == "directed":
        turn_instructions = build_directed_pulse_instructions(state, base_instructions)
        clear_pending = True
    else:
        turn_instructions = build_conversational_pulse_instructions(state, base_instructions)
        clear_pending = False

    with _lock:
        _active_pulse_turn = ActivePulseTurn(
            memory_root=memory_root.resolve(),
            persona_namespace=persona_namespace,
            mode=mode,
            clear_pending_cue=clear_pending,
        )
        _pulse_turn_text = []

    state.pulse_in_flight = True
    save_pulse_state(memory_root, persona_namespace, state)

    text_prompt_queue.put(
        GenerateResponseRequest(
            runtime_config=runtime_config,
            response=RealtimeResponseCreateParams(instructions=turn_instructions),
            turn_id=None,
            turn_revision=None,
        )
    )
    logger.info(
        "Pulse turn injected mode=%r namespace=%r skill=%r",
        mode,
        persona_namespace,
        state.skill_name,
    )
    return True


def evaluate_and_maybe_inject_pulse(
    *,
    memory_root: Path,
    persona_namespace: str,
    state: PulseState,
    session: SessionConfig,
    text_prompt_queue: Queue[Any] | None,
    runtime_config: RuntimeConfig | None,
    should_listen: Event | None,
) -> bool:
    if text_prompt_queue is None or runtime_config is None:
        logger.warning("Pulse injection skipped: scheduler not configured with runtime_config/queue")
        return False

    mode = select_pulse_mode(state, session, should_listen=should_listen)
    if mode is None:
        return False

    return inject_pulse_turn(
        memory_root=memory_root,
        persona_namespace=persona_namespace,
        state=state,
        mode=mode,  # type: ignore[arg-type]
        text_prompt_queue=text_prompt_queue,
        runtime_config=runtime_config,
    )


def is_no_output_text(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return True
    normalized = cleaned.replace(" ", "")
    return normalized.upper() == NO_OUTPUT_MARKER.upper()


def handle_pulse_response_chunk(chunk: LLMResponseChunk) -> LLMResponseChunk | None:
    """Suppress TTS chunks for conversational [NO_OUTPUT] pulse turns."""
    global _pulse_turn_text

    with _lock:
        active = _active_pulse_turn
        if active is None:
            return chunk
        if chunk.text:
            _pulse_turn_text.append(chunk.text)
        combined = "".join(_pulse_turn_text).strip()
        if active.mode == "conversational" and is_no_output_text(combined):
            return None
        if active.mode == "directed" and active.clear_pending_cue and is_no_output_text(combined):
            logger.warning("Directed pulse returned [NO_OUTPUT]; suppressing empty delivery")
            return None
    return chunk


def handle_pulse_end_of_response() -> None:
    """Finalize pulse turn state after the LLM finishes responding."""
    global _active_pulse_turn, _pulse_turn_text

    with _lock:
        active = _active_pulse_turn
        full_text = "".join(_pulse_turn_text).strip()
        _active_pulse_turn = None
        _pulse_turn_text = []

    if active is None:
        return

    spoke = bool(full_text) and not is_no_output_text(full_text)
    _complete_pulse_turn(
        memory_root=active.memory_root,
        persona_namespace=active.persona_namespace,
        mode=active.mode,
        clear_pending_cue=active.clear_pending_cue and spoke,
        spoke=spoke,
        full_text=full_text,
    )


def record_assistant_speech_for_active_pulse(full_text: str) -> None:
    """Track last_assistant_speech_at for reactive turns during an active pulse session."""
    if is_no_output_text(full_text):
        return
    try:
        profile = get_active_personality()
        memory_root = _memory_root()
        state = load_pulse_state(memory_root, profile.memory_namespace)
        if state is None or state.status != "active":
            return
        state.last_assistant_speech_at = _utc_now_iso()
        save_pulse_state(memory_root, profile.memory_namespace, state)
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("Could not record assistant speech on pulse state: %s", exc)


def reset_pulse_inject_for_tests() -> None:
    global _active_pulse_turn, _pulse_turn_text
    with _lock:
        _active_pulse_turn = None
        _pulse_turn_text = []

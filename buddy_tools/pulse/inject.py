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

from openai.types.realtime.conversation_item import RealtimeConversationItemUserMessage
from openai.types.realtime.realtime_response_create_params import RealtimeResponseCreateParams
from openai.types.realtime.realtime_conversation_item_user_message import Content as UserContent
from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig
from speech_to_speech.pipeline.messages import GenerateResponseRequest, LLMResponseChunk

from buddy_tools.personality import get_active_personality
from buddy_tools.pulse.gates import (
    mark_fold_on_speech_deferral,
    select_pulse_mode,
    set_last_user_speech_stopped_at,
)
from buddy_tools.pulse.schema import SessionConfig
from buddy_tools.pulse.state import PulseState, load_pulse_state, save_pulse_state

logger = logging.getLogger(__name__)

PULSE_NUDGE_PREFIX = "[Pulse — internal scheduled nudge, not user speech]: "
NO_OUTPUT_MARKER = "[NO_OUTPUT]"

PulseTurnMode = Literal["directed", "conversational", "fold"]


@dataclass
class ActivePulseTurn:
    memory_root: Path
    persona_namespace: str
    mode: PulseTurnMode
    clear_pending_cue: bool


_lock = Lock()
_active_pulse_turn: ActivePulseTurn | None = None
_pulse_turn_text: list[str] = []
_pulse_cancel_scope: Any | None = None
_pulse_audio_out_queue: Any | None = None


def set_pulse_cancel_scope(cancel_scope: Any | None) -> None:
    """Share pipeline CancelScope so in-flight pulse turns can be interrupted."""
    global _pulse_cancel_scope
    _pulse_cancel_scope = cancel_scope


def set_pulse_audio_out_queue(audio_out_queue: Any | None) -> None:
    """Local playback queue — drained when aborting a directed pulse over user speech."""
    global _pulse_audio_out_queue
    _pulse_audio_out_queue = audio_out_queue


def get_pulse_cancel_scope() -> Any | None:
    return _pulse_cancel_scope


def _drain_local_audio_output() -> None:
    """Drop queued PCM and signal response-done so listening can resume."""
    from queue import Empty

    from speech_to_speech.pipeline.messages import AUDIO_RESPONSE_DONE

    queue = _pulse_audio_out_queue
    if queue is None:
        return
    drained = 0
    while True:
        try:
            queue.get_nowait()
            drained += 1
        except Empty:
            break
    try:
        queue.put_nowait(AUDIO_RESPONSE_DONE)
    except Exception:
        logger.debug("Could not enqueue AUDIO_RESPONSE_DONE after pulse abort", exc_info=True)
    if drained:
        logger.info("Drained %d queued audio chunk(s) after pulse abort", drained)


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
        "fold_on_next_reply": state.fold_on_next_reply,
        "vars": state.vars,
        "fired_rules": state.fired_rules,
        "last_user_speech_at": state.last_user_speech_at,
        "last_assistant_speech_at": state.last_assistant_speech_at,
    }


def build_directed_pulse_instructions(state: PulseState, base_instructions: str) -> str:
    snapshot = json.dumps(_state_snapshot(state), indent=2, sort_keys=True)
    return (
        f"{base_instructions}\n\n"
        "Pulse directed turn — deliver all pending cues naturally in spoken language.\n"
        f"Pending cue(s): {state.pending_cue}\n"
        "Cover every directive above in one response. Do not call tools on this turn. "
        "Do not invent cues beyond the pending cues.\n"
        f"Pulse state snapshot:\n{snapshot}"
    )


def build_fold_cue_instructions(state: PulseState, base_instructions: str) -> str:
    """Instructions for weaving a speech-deferred mandatory cue into a reactive reply."""
    snapshot = json.dumps(_state_snapshot(state), indent=2, sort_keys=True)
    cue = (state.pending_cue or "").strip()
    return (
        f"{base_instructions}\n\n"
        "## REQUIRED — Pulse fold-into-reply (overrides waiting / stay-quiet guidance)\n"
        "This turn MUST deliver every pending mandatory cue in spoken words. "
        "Respond to the user, and weave the cue(s) into the same reply — "
        "do not make the cue the entire response, but do not omit it.\n"
        f"Pending cue(s) you MUST speak this turn: {cue}\n"
        "Example shape: acknowledge the user → deliver the cue naturally "
        '(e.g. "by the way, switch to camera two for the close-up") → continue.\n'
        "Do not invent cues beyond the pending cues. Do not claim you will deliver "
        "the cue later — speak it now.\n"
        f"Pulse state snapshot:\n{snapshot}"
    )


def build_conversational_pulse_instructions(
    state: PulseState,
    base_instructions: str,
    *,
    scene_attached: bool = False,
) -> str:
    snapshot = json.dumps(_state_snapshot(state), indent=2, sort_keys=True)
    scene_note = ""
    if scene_attached:
        scene_note = (
            "A fresh webcam snapshot is attached to this turn. "
            "Use it only for brief, relevant observations about what the user is doing. "
            "Do not invent director cues or camera switches from the image.\n"
        )
    return (
        f"{base_instructions}\n\n"
        "Pulse conversational turn — optionally speak briefly to maintain engagement during a lull.\n"
        f"{scene_note}"
        f"If silence is appropriate, output exactly {NO_OUTPUT_MARKER} and nothing else.\n"
        "Do not call tools. Do not invent scheduled cues, camera switches, or mandatory directives.\n"
        f"Pulse state snapshot:\n{snapshot}"
    )


def _should_attach_scene_capture(
    session: SessionConfig | None,
    mode: PulseTurnMode,
    state: PulseState,
) -> bool:
    if session is None or mode != "conversational":
        return False
    if state.narrator_muted:
        return False
    return session.pulse.scene_capture == "conversational"


def _try_capture_scene() -> str | None:
    try:
        from buddy_tools.media.camera import capture_frame

        return capture_frame().preview_data_uri
    except Exception as exc:
        logger.warning("Pulse scene capture failed; continuing without image: %s", exc)
        return None


def _make_pulse_nudge_message(
    nudge: str,
    *,
    image_data_uri: str | None = None,
) -> RealtimeConversationItemUserMessage:
    text = f"{PULSE_NUDGE_PREFIX}{nudge}"
    content: list[UserContent] = [UserContent(type="input_text", text=text)]
    if image_data_uri:
        content.append(
            UserContent(type="input_image", image_url=image_data_uri, detail="auto")
        )
    return RealtimeConversationItemUserMessage(type="message", role="user", content=content)


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
        state.fold_on_next_reply = False
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
    session: SessionConfig | None = None,
) -> bool:
    global _active_pulse_turn, _pulse_turn_text

    nudge = (
        f"Directed pulse: deliver all pending cues — {state.pending_cue}"
        if mode == "directed"
        else "Conversational pulse: speak briefly or output [NO_OUTPUT]."
    )

    scene_attached = False
    image_data_uri: str | None = None
    if _should_attach_scene_capture(session, mode, state):
        image_data_uri = _try_capture_scene()
        scene_attached = image_data_uri is not None

    try:
        runtime_config.chat.add_item(
            _make_pulse_nudge_message(nudge, image_data_uri=image_data_uri)
        )
    except Exception:
        logger.exception("Pulse turn failed to add nudge message to chat")
        return False

    base_instructions = runtime_config.session.instructions or ""
    if mode == "directed":
        turn_instructions = build_directed_pulse_instructions(state, base_instructions)
        clear_pending = True
    else:
        turn_instructions = build_conversational_pulse_instructions(
            state,
            base_instructions,
            scene_attached=scene_attached,
        )
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
        "Pulse turn injected mode=%r namespace=%r skill=%r scene_attached=%s",
        mode,
        persona_namespace,
        state.skill_name,
        scene_attached,
    )
    return True


def begin_fold_cue_delivery(
    *,
    memory_root: Path,
    persona_namespace: str,
    state: PulseState,
) -> bool:
    """Register a fold-into-reply delivery for the upcoming reactive turn.

    Sets pulse_in_flight so the worker cannot also directed-inject the same cue.
    """
    global _active_pulse_turn, _pulse_turn_text

    if not state.pending_cue or not state.pending_cue.strip():
        return False
    if not state.fold_on_next_reply:
        return False
    if state.narrator_muted:
        return False
    if state.pulse_in_flight:
        return False

    with _lock:
        if _active_pulse_turn is not None:
            return False
        _active_pulse_turn = ActivePulseTurn(
            memory_root=memory_root.resolve(),
            persona_namespace=persona_namespace,
            mode="fold",
            clear_pending_cue=True,
        )
        _pulse_turn_text = []

    state.pulse_in_flight = True
    save_pulse_state(memory_root, persona_namespace, state)
    logger.info(
        "Fold cue delivery started namespace=%r skill=%r cue=%r",
        persona_namespace,
        state.skill_name,
        (state.pending_cue or "")[:80],
    )
    return True


def is_active_pulse_turn() -> bool:
    """True while a directed/conversational/fold pulse turn is in flight."""
    with _lock:
        return _active_pulse_turn is not None


def abort_in_flight_pulse_for_user_speech() -> bool:
    """Cancel a directed/conversational pulse that would talk over the user.

    Converts a mandatory pending cue to fold-into-next-reply so it rides the
    user's next natural answer instead of finishing as a separate inject.
    Returns True when an in-flight pulse was aborted.
    """
    global _active_pulse_turn, _pulse_turn_text

    with _lock:
        active = _active_pulse_turn
        if active is None or active.mode not in ("directed", "conversational"):
            return False
        memory_root = active.memory_root
        persona_namespace = active.persona_namespace
        mode = active.mode
        _active_pulse_turn = None
        _pulse_turn_text = []

    if _pulse_cancel_scope is not None:
        try:
            _pulse_cancel_scope.cancel()
        except Exception:
            logger.exception("Failed to cancel pipeline after user speech during pulse")

    _drain_local_audio_output()

    state = load_pulse_state(memory_root, persona_namespace)
    if state is None:
        return True

    state.pulse_in_flight = False
    if mode == "directed" and state.pending_cue and state.pending_cue.strip():
        state.fold_on_next_reply = True
        state.cue_priority = "mandatory"
        logger.info(
            "Aborted directed pulse for user speech; cue will fold into next reply cue=%r",
            state.pending_cue[:80],
        )
    else:
        logger.info("Aborted %s pulse for user speech skill=%r", mode, state.skill_name)

    save_pulse_state(memory_root, persona_namespace, state)
    return True


def prepare_fold_cue_commit_instructions(runtime_config: RuntimeConfig) -> str | None:
    """If a speech-deferred mandatory cue should fold into this commit, begin delivery.

    Returns turn instructions to attach on GenerateResponseRequest, or None.
    """
    try:
        profile = get_active_personality()
        memory_root = _memory_root()
        state = load_pulse_state(memory_root, profile.memory_namespace)
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.debug("Fold cue commit skipped: %s", exc)
        return None

    if state is None or state.status != "active":
        return None
    if not state.fold_on_next_reply:
        return None
    if not state.pending_cue or state.cue_priority != "mandatory":
        return None

    if not begin_fold_cue_delivery(
        memory_root=memory_root,
        persona_namespace=profile.memory_namespace,
        state=state,
    ):
        return None

    # Mirror directed inject: put the cue in chat history so the model cannot miss it.
    nudge = f"Fold into this reply — deliver all pending cues: {state.pending_cue}"
    try:
        runtime_config.chat.add_item(_make_pulse_nudge_message(nudge))
    except Exception:
        logger.exception("Fold cue failed to add nudge message to chat")

    base_instructions = runtime_config.session.instructions or ""
    return build_fold_cue_instructions(state, base_instructions)


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

    # Prefer in-memory active turn (covers fold started on the commit path while
    # the worker still holds a stale pulse_in_flight=False snapshot).
    if is_active_pulse_turn() or state.pulse_in_flight:
        return False

    mark_fold_on_speech_deferral(state, session, should_listen=should_listen)

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
        session=session,
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
        if (
            active.mode in ("directed", "fold")
            and active.clear_pending_cue
            and is_no_output_text(combined)
        ):
            logger.warning(
                "%s pulse returned [NO_OUTPUT]; suppressing empty delivery",
                active.mode.capitalize(),
            )
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
    global _active_pulse_turn, _pulse_turn_text, _pulse_cancel_scope, _pulse_audio_out_queue
    with _lock:
        _active_pulse_turn = None
        _pulse_turn_text = []
    _pulse_cancel_scope = None
    _pulse_audio_out_queue = None

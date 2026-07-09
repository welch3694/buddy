"""Pulse session runtime — worker tick loop and per-persona state."""

from buddy_tools.pulse.config_merge import apply_pulse_config, merge_pulse_params
from buddy_tools.pulse.gates import reset_pulse_gates_for_tests
from buddy_tools.pulse.inject import (
    NO_OUTPUT_MARKER,
    PULSE_NUDGE_PREFIX,
    build_conversational_pulse_instructions,
    build_directed_pulse_instructions,
    evaluate_and_maybe_inject_pulse,
    handle_pulse_end_of_response,
    handle_pulse_response_chunk,
    inject_pulse_turn,
    is_no_output_text,
    record_assistant_speech_for_active_pulse,
    record_user_speech,
    reset_pulse_inject_for_tests,
)
from buddy_tools.pulse.rules import evaluate_condition, evaluate_pulse_tick
from buddy_tools.pulse.schema import (
    SessionConfig,
    SessionValidationError,
    load_session_config,
    parse_session_config,
    session_config_from_dict,
    session_config_to_dict,
)
from buddy_tools.pulse.state import (
    PulseState,
    build_pulse_state_from_session,
    clear_pulse_state,
    init_pulse_state_from_skill,
    load_pulse_state,
    pulse_state_path,
    save_pulse_state,
)
from buddy_tools.pulse.template import render_session_template
from buddy_tools.pulse.worker import (
    configure_pulse,
    reset_pulse_workers_for_tests,
    start_pulse_worker,
    stop_pulse_worker,
)

__all__ = [
    "NO_OUTPUT_MARKER",
    "PULSE_NUDGE_PREFIX",
    "PulseState",
    "SessionConfig",
    "SessionValidationError",
    "apply_pulse_config",
    "build_conversational_pulse_instructions",
    "build_directed_pulse_instructions",
    "build_pulse_state_from_session",
    "clear_pulse_state",
    "configure_pulse",
    "evaluate_and_maybe_inject_pulse",
    "evaluate_condition",
    "evaluate_pulse_tick",
    "handle_pulse_end_of_response",
    "handle_pulse_response_chunk",
    "init_pulse_state_from_skill",
    "inject_pulse_turn",
    "is_no_output_text",
    "load_pulse_state",
    "load_session_config",
    "merge_pulse_params",
    "parse_session_config",
    "pulse_state_path",
    "record_assistant_speech_for_active_pulse",
    "record_user_speech",
    "render_session_template",
    "reset_pulse_gates_for_tests",
    "reset_pulse_inject_for_tests",
    "reset_pulse_workers_for_tests",
    "save_pulse_state",
    "session_config_from_dict",
    "session_config_to_dict",
    "start_pulse_worker",
    "stop_pulse_worker",
]

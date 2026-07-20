"""Localhost companion status bridge (WebSocket event publisher)."""

from buddy_tools.companion.bridge import (
    CompanionBridge,
    create_and_start_companion_bridge,
    get_companion_bridge,
    reset_companion_bridge_for_tests,
)
from buddy_tools.companion.config import CompanionBridgeConfig, load_companion_bridge_config
from buddy_tools.companion.publisher import (
    CompanionEventPublisher,
    emit_assistant_text,
    emit_pulse_state,
    emit_speaking_progress,
    emit_turn_state,
    get_companion_publisher,
    reset_companion_publisher_for_tests,
)

__all__ = [
    "CompanionBridge",
    "CompanionBridgeConfig",
    "CompanionEventPublisher",
    "create_and_start_companion_bridge",
    "emit_assistant_text",
    "emit_pulse_state",
    "emit_speaking_progress",
    "emit_turn_state",
    "get_companion_bridge",
    "get_companion_publisher",
    "load_companion_bridge_config",
    "reset_companion_bridge_for_tests",
    "reset_companion_publisher_for_tests",
]

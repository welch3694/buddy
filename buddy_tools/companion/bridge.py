"""Start the companion status bridge when configured (#115)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from buddy_tools.companion.config import CompanionBridgeConfig, load_companion_bridge_config
from buddy_tools.companion.publisher import (
    CompanionEventPublisher,
    set_companion_publisher,
)
from buddy_tools.companion.pulse_watch import PulseStateWatcher
from buddy_tools.companion.server import CompanionBridgeServer
from buddy_tools.voice.turn_state import current_turn_state

logger = logging.getLogger(__name__)

_bridge: CompanionBridge | None = None


class CompanionBridge:
    """Owns publisher, WebSocket server, and pulse watcher lifecycle."""

    def __init__(
        self,
        config: CompanionBridgeConfig,
        *,
        memory_root: Path,
        persona_namespace: str,
        stop_event: threading.Event,
    ) -> None:
        self.config = config
        self.publisher = CompanionEventPublisher()
        self.server = CompanionBridgeServer(config, self.publisher, stop_event=stop_event)
        self.pulse_watcher = PulseStateWatcher(
            memory_root=memory_root,
            persona_namespace=persona_namespace,
            publisher=self.publisher,
            stop_event=stop_event,
        )

    def start(self) -> None:
        set_companion_publisher(self.publisher)
        # Seed turn_state so clients connecting mid-session get current status.
        self.publisher.emit_turn_state(current_turn_state().value, reason="bridge_start")
        self.pulse_watcher.start()
        self.server.start()


def get_companion_bridge() -> CompanionBridge | None:
    return _bridge


def set_companion_bridge(bridge: CompanionBridge | None) -> None:
    global _bridge
    _bridge = bridge


def create_and_start_companion_bridge(
    *,
    memory_root: Path,
    persona_namespace: str,
    stop_event: threading.Event,
) -> CompanionBridge | None:
    """Create and start the companion bridge when ``BUDDY_COMPANION_BRIDGE`` is set."""
    config = load_companion_bridge_config()
    if config is None:
        return None

    bridge = CompanionBridge(
        config,
        memory_root=memory_root,
        persona_namespace=persona_namespace,
        stop_event=stop_event,
    )
    bridge.start()
    set_companion_bridge(bridge)
    logger.info(
        "Companion status bridge enabled (%s, persona=%r)",
        config.url,
        persona_namespace,
    )
    return bridge


def reset_companion_bridge_for_tests() -> None:
    set_companion_bridge(None)
    set_companion_publisher(None)

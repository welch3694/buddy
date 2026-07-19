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
        personality_id: str,
        persona_name: str,
        voice_id: str | None = None,
    ) -> None:
        self.config = config
        self.personality_id = personality_id
        self.persona_name = persona_name
        self.persona_namespace = persona_namespace
        self.voice_id = voice_id
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
        self.publisher.emit_persona(
            personality_id=self.personality_id,
            name=self.persona_name,
            memory_namespace=self.persona_namespace,
            voice_id=self.voice_id,
        )
        # Seed turn_state so clients connecting mid-session get current status.
        self.publisher.emit_turn_state(current_turn_state().value, reason="bridge_start")
        self.pulse_watcher.start()
        self.server.start()

    def set_active_persona(
        self,
        *,
        personality_id: str,
        persona_name: str,
        persona_namespace: str,
        voice_id: str | None = None,
    ) -> None:
        """Update cached persona, pulse watch target, and broadcast to clients."""
        self.personality_id = personality_id
        self.persona_name = persona_name
        self.persona_namespace = persona_namespace
        self.voice_id = voice_id
        self.pulse_watcher.set_persona_namespace(persona_namespace)
        self.publisher.emit_persona(
            personality_id=personality_id,
            name=persona_name,
            memory_namespace=persona_namespace,
            voice_id=voice_id,
        )


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
    personality_id: str,
    persona_name: str,
    voice_id: str | None = None,
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
        personality_id=personality_id,
        persona_name=persona_name,
        voice_id=voice_id,
    )
    bridge.start()
    set_companion_bridge(bridge)
    logger.info(
        "Companion status bridge enabled (%s, persona=%r id=%r)",
        config.url,
        persona_name,
        personality_id,
    )
    return bridge


def reset_companion_bridge_for_tests() -> None:
    set_companion_bridge(None)
    set_companion_publisher(None)

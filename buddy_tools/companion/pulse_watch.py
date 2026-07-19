"""Poll pulse_state.json and publish salient snapshots (#115)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from buddy_tools.companion.events import salient_pulse_snapshot
from buddy_tools.companion.publisher import CompanionEventPublisher
from buddy_tools.pulse.state import load_pulse_state, pulse_state_path

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 0.5


class PulseStateWatcher:
    """Daemon thread that polls pulse state and emits on change."""

    def __init__(
        self,
        *,
        memory_root: Path,
        persona_namespace: str,
        publisher: CompanionEventPublisher,
        stop_event: threading.Event,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self.memory_root = memory_root
        self.persona_namespace = persona_namespace
        self.publisher = publisher
        self.stop_event = stop_event
        self.poll_interval_s = max(0.1, poll_interval_s)
        self._thread: threading.Thread | None = None
        self._last_fingerprint: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="companion-pulse-watch",
            daemon=True,
        )
        self._thread.start()

    def _fingerprint(self, snapshot: dict[str, Any], path: Path) -> str:
        import json

        try:
            mtime = path.stat().st_mtime_ns if path.is_file() else 0
        except OSError:
            mtime = 0
        # Exclude volatile ts so mtime/content drive change detection.
        body = {key: value for key, value in snapshot.items() if key != "ts"}
        return f"{mtime}:{json.dumps(body, sort_keys=True, default=str)}"

    def _run(self) -> None:
        path = pulse_state_path(self.memory_root, self.persona_namespace)
        logger.debug("Companion pulse watcher polling %s", path)
        while not self.stop_event.is_set():
            try:
                state = load_pulse_state(self.memory_root, self.persona_namespace)
                snapshot = salient_pulse_snapshot(state)
                fingerprint = self._fingerprint(snapshot, path)
                if fingerprint != self._last_fingerprint:
                    self._last_fingerprint = fingerprint
                    self.publisher.emit(snapshot)
            except Exception:
                logger.exception("Companion pulse watcher failed")
            self.stop_event.wait(self.poll_interval_s)

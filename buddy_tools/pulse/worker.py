"""Background pulse worker — silent tick loop per persona namespace."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Event, Lock
from typing import Any

from speech_to_speech.api.openai_realtime.runtime_config import RuntimeConfig

from buddy_tools.pulse.inject import evaluate_and_maybe_inject_pulse
from buddy_tools.pulse.rules import evaluate_pulse_tick
from buddy_tools.pulse.state import PulseState, load_pulse_state, save_pulse_state

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 5.0
MIN_TICK_INTERVAL_SECONDS = 0.05


@dataclass
class _ActivePulseWorker:
    memory_root: Path
    persona_namespace: str
    skill_name: str
    tick_interval_seconds: float
    stop_event: Event
    thread: threading.Thread | None = None


class PulseWorkerManager:
    """Thread-safe registry of per-persona pulse workers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._workers: dict[str, _ActivePulseWorker] = {}
        self.text_prompt_queue: Queue[Any] | None = None
        self.runtime_config: RuntimeConfig | None = None
        self.should_listen: Event | None = None

    def configure(
        self,
        *,
        text_prompt_queue: Queue[Any] | None,
        runtime_config: RuntimeConfig | None,
        should_listen: Event | None,
    ) -> None:
        with self._lock:
            self.text_prompt_queue = text_prompt_queue
            self.runtime_config = runtime_config
            self.should_listen = should_listen

    def start(
        self,
        memory_root: Path,
        persona_namespace: str,
        skill_name: str,
        *,
        tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
    ) -> None:
        interval = max(MIN_TICK_INTERVAL_SECONDS, float(tick_interval_seconds))
        with self._lock:
            existing = self._workers.get(persona_namespace)
            if existing is not None:
                self._stop_worker(existing)

            stop_event = Event()
            worker = _ActivePulseWorker(
                memory_root=memory_root.resolve(),
                persona_namespace=persona_namespace,
                skill_name=skill_name,
                tick_interval_seconds=interval,
                stop_event=stop_event,
            )
            thread = threading.Thread(
                target=self._run_loop,
                args=(worker,),
                name=f"pulse-{persona_namespace}",
                daemon=True,
            )
            worker.thread = thread
            self._workers[persona_namespace] = worker
            thread.start()
            logger.info(
                "Started pulse worker for namespace=%r skill=%r interval=%.2fs",
                persona_namespace,
                skill_name,
                interval,
            )

    def stop(self, persona_namespace: str) -> bool:
        with self._lock:
            worker = self._workers.pop(persona_namespace, None)
            if worker is None:
                return False
            self._stop_worker(worker)
            return True

    def stop_all(self) -> int:
        with self._lock:
            namespaces = list(self._workers.keys())
            for namespace in namespaces:
                worker = self._workers.pop(namespace)
                self._stop_worker(worker)
            return len(namespaces)

    @staticmethod
    def _stop_worker(worker: _ActivePulseWorker) -> None:
        worker.stop_event.set()
        thread = worker.thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        logger.info(
            "Stopped pulse worker for namespace=%r skill=%r",
            worker.persona_namespace,
            worker.skill_name,
        )

    def _run_loop(self, worker: _ActivePulseWorker) -> None:
        while not worker.stop_event.is_set():
            if worker.stop_event.wait(worker.tick_interval_seconds):
                break
            try:
                self._tick(worker)
            except Exception:
                logger.exception(
                    "Pulse worker tick failed for namespace=%r skill=%r",
                    worker.persona_namespace,
                    worker.skill_name,
                )

    def _tick(self, worker: _ActivePulseWorker) -> None:
        state = load_pulse_state(worker.memory_root, worker.persona_namespace)
        if state is None:
            logger.warning(
                "Pulse worker tick skipped: no pulse state for namespace=%r",
                worker.persona_namespace,
            )
            self.stop(worker.persona_namespace)
            return

        if state.skill_name != worker.skill_name:
            logger.warning(
                "Pulse worker skill mismatch for namespace=%r: worker=%r state=%r",
                worker.persona_namespace,
                worker.skill_name,
                state.skill_name,
            )
            self.stop(worker.persona_namespace)
            return

        if state.status != "active":
            logger.info(
                "Pulse worker tick skipped (status=%r) for namespace=%r",
                state.status,
                worker.persona_namespace,
            )
            return

        state.tick_count += 1
        from datetime import UTC, datetime

        state.last_tick_at = datetime.now(UTC).replace(microsecond=0).isoformat()

        session = state.get_session_config()
        if session is not None:
            evaluate_pulse_tick(state, session)
            evaluate_and_maybe_inject_pulse(
                memory_root=worker.memory_root,
                persona_namespace=worker.persona_namespace,
                state=state,
                session=session,
                text_prompt_queue=self.text_prompt_queue,
                runtime_config=self.runtime_config,
                should_listen=self.should_listen,
            )

        save_pulse_state(worker.memory_root, worker.persona_namespace, state)
        logger.info(
            "Pulse tick namespace=%r skill=%r count=%d phase=%r pending_cue=%r",
            worker.persona_namespace,
            state.skill_name,
            state.tick_count,
            state.phase,
            state.pending_cue,
        )


_manager: PulseWorkerManager | None = None


def get_pulse_worker_manager() -> PulseWorkerManager:
    global _manager
    if _manager is None:
        _manager = PulseWorkerManager()
    return _manager


def reset_pulse_workers_for_tests() -> None:
    global _manager
    if _manager is not None:
        _manager.stop_all()
        _manager = None


def configure_pulse(
    *,
    text_prompt_queue: Queue[Any] | None,
    runtime_config: RuntimeConfig | None,
    should_listen: Event | None,
) -> PulseWorkerManager:
    manager = get_pulse_worker_manager()
    manager.configure(
        text_prompt_queue=text_prompt_queue,
        runtime_config=runtime_config,
        should_listen=should_listen,
    )
    return manager


def start_pulse_worker(
    memory_root: Path,
    persona_namespace: str,
    skill_name: str,
    *,
    tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
) -> None:
    get_pulse_worker_manager().start(
        memory_root,
        persona_namespace,
        skill_name,
        tick_interval_seconds=tick_interval_seconds,
    )


def stop_pulse_worker(persona_namespace: str) -> bool:
    return get_pulse_worker_manager().stop(persona_namespace)

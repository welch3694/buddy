"""Background consolidation worker — processes session close jobs off the hot path."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, PriorityQueue
from typing import Any

from buddy_tools.episodic.consolidation import consolidate_session
from buddy_tools.episodic.config import EpisodicConfig, load_episodic_config
from buddy_tools.episodic.paths import episodic_root, session_json_path
from buddy_tools.episodic.session import find_session_json_files, load_session
from buddy_tools.infra.llm_client import LlmFn

logger = logging.getLogger(__name__)

_AGENT_BUSY_BACKOFF_SECONDS = 5.0
_MAX_RETRY_BACKOFF_SECONDS = 600.0
_JOB_COUNTER = 0
_JOB_COUNTER_LOCK = threading.Lock()


def _next_job_seq() -> int:
    global _JOB_COUNTER
    with _JOB_COUNTER_LOCK:
        _JOB_COUNTER += 1
        return _JOB_COUNTER


@dataclass(order=True)
class _QueuedJob:
    run_at: float
    seq: int
    session_dir: Path = field(compare=False)
    memory_root: Path = field(compare=False)
    persona_namespace: str = field(compare=False)
    session_id: str = field(compare=False)
    attempt: int = field(compare=False, default=0)
    cancelled: bool = field(compare=False, default=False)


class ConsolidationWorkerManager:
    """Single-threaded worker that processes consolidation jobs one at a time."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queue: PriorityQueue[_QueuedJob] = PriorityQueue()
        self._pending_session_ids: set[str] = set()
        self._cancelled_session_ids: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._agent_busy_fn: Callable[[], bool] = lambda: False
        self._llm_fn: LlmFn | None = None
        self._config: EpisodicConfig | None = None
        self._memory_root: Path | None = None
        self._persona_namespace: str | None = None

    def configure(
        self,
        memory_root: Path,
        persona_namespace: str,
        *,
        agent_busy_fn: Callable[[], bool] | None = None,
        config: EpisodicConfig | None = None,
        llm_fn: LlmFn | None = None,
    ) -> None:
        with self._lock:
            self._memory_root = memory_root.resolve()
            self._persona_namespace = persona_namespace
            self._agent_busy_fn = agent_busy_fn or (lambda: False)
            self._config = config or load_episodic_config()
            self._llm_fn = llm_fn
            self._ensure_thread()

    def enqueue_session_close(
        self,
        session_dir: Path,
        memory_root: Path,
        persona_namespace: str,
        *,
        delay_seconds: float | None = None,
        attempt: int = 0,
    ) -> None:
        session_path = session_json_path(session_dir)
        session = load_session(session_path)
        if session is None:
            logger.warning("Cannot enqueue consolidation — missing session: %s", session_path)
            return

        session_id = session.session_id
        with self._lock:
            if session_id in self._pending_session_ids:
                logger.debug("Consolidation already queued for session %r", session_id)
                return
            self._pending_session_ids.add(session_id)
            self._cancelled_session_ids.discard(session_id)

            cfg = self._config or load_episodic_config()
            delay = delay_seconds if delay_seconds is not None else float(cfg.consolidation_delay_seconds)
            run_at = time.monotonic() + max(0.0, delay)
            job = _QueuedJob(
                run_at=run_at,
                seq=_next_job_seq(),
                session_dir=session_dir.resolve(),
                memory_root=memory_root.resolve(),
                persona_namespace=persona_namespace,
                session_id=session_id,
                attempt=attempt,
            )
            self._queue.put(job)
            self._ensure_thread()
            logger.info(
                "Enqueued consolidation for session %r (delay=%.1fs attempt=%d)",
                session_id,
                delay,
                attempt,
            )

    def cancel_job(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._pending_session_ids:
                return False
            self._cancelled_session_ids.add(session_id)
            self._pending_session_ids.discard(session_id)
            logger.info("Cancelled consolidation job for session %r", session_id)
            return True

    def is_job_pending(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._pending_session_ids

    def scan_and_enqueue_pending(
        self,
        memory_root: Path,
        persona_namespace: str,
    ) -> int:
        """Enqueue all close_pending sessions found on disk."""
        tree = episodic_root(memory_root, persona_namespace)
        count = 0
        for path in find_session_json_files(tree):
            session = load_session(path)
            if session is None or session.status != "close_pending":
                continue
            self.enqueue_session_close(
                path.parent,
                memory_root,
                persona_namespace,
                delay_seconds=0.0,
            )
            count += 1
        return count

    def shutdown(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._queue.put(
                _QueuedJob(
                    run_at=0.0,
                    seq=_next_job_seq(),
                    session_dir=Path("."),
                    memory_root=Path("."),
                    persona_namespace="",
                    session_id="__shutdown__",
                )
            )
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None

    def process_all_sync(self, *, timeout: float = 30.0) -> None:
        """Drain the queue synchronously (for tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                pending = len(self._pending_session_ids)
            if pending == 0 and self._queue.empty():
                break
            time.sleep(0.05)
        if self._pending_session_ids:
            raise TimeoutError(
                f"Consolidation jobs still pending after {timeout}s: {self._pending_session_ids}"
            )

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run_loop,
            name="episodic-consolidation",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except Empty:
                continue

            if job.session_id == "__shutdown__":
                break

            now = time.monotonic()
            if job.run_at > now:
                wait_seconds = job.run_at - now
                self._queue.put(job)
                # Interruptible wait so shutdown does not block on delay sleep.
                if self._stop_event.wait(timeout=min(wait_seconds, 1.0)):
                    break
                continue

            if job.session_id in self._cancelled_session_ids:
                with self._lock:
                    self._cancelled_session_ids.discard(job.session_id)
                continue

            if self._agent_busy_fn():
                job.run_at = time.monotonic() + _AGENT_BUSY_BACKOFF_SECONDS
                self._queue.put(job)
                if self._stop_event.wait(timeout=0.5):
                    break
                continue

            success = consolidate_session(
                job.session_dir,
                job.memory_root,
                job.persona_namespace,
                llm_fn=self._llm_fn,
            )

            with self._lock:
                self._pending_session_ids.discard(job.session_id)

            if not success and not self._stop_event.is_set():
                cfg = self._config or load_episodic_config()
                backoff = min(
                    cfg.consolidation_retry_base_seconds * (2**job.attempt),
                    _MAX_RETRY_BACKOFF_SECONDS,
                )
                self.enqueue_session_close(
                    job.session_dir,
                    job.memory_root,
                    job.persona_namespace,
                    delay_seconds=backoff,
                    attempt=job.attempt + 1,
                )


_manager: ConsolidationWorkerManager | None = None


def get_consolidation_worker() -> ConsolidationWorkerManager:
    global _manager
    if _manager is None:
        _manager = ConsolidationWorkerManager()
    return _manager


def configure_consolidation_worker(
    memory_root: Path,
    persona_namespace: str,
    *,
    agent_busy_fn: Callable[[], bool] | None = None,
    config: EpisodicConfig | None = None,
    llm_fn: LlmFn | None = None,
) -> ConsolidationWorkerManager:
    manager = get_consolidation_worker()
    manager.configure(
        memory_root,
        persona_namespace,
        agent_busy_fn=agent_busy_fn,
        config=config,
        llm_fn=llm_fn,
    )
    manager.scan_and_enqueue_pending(memory_root, persona_namespace)
    return manager


def enqueue_session_consolidation(
    session_dir: Path,
    memory_root: Path,
    persona_namespace: str,
    *,
    delay_seconds: float | None = None,
) -> None:
    get_consolidation_worker().enqueue_session_close(
        session_dir,
        memory_root,
        persona_namespace,
        delay_seconds=delay_seconds,
    )


def cancel_consolidation_job(session_id: str) -> bool:
    return get_consolidation_worker().cancel_job(session_id)


def shutdown_consolidation_worker() -> None:
    global _manager
    if _manager is not None:
        _manager.shutdown()
        _manager = None


def reset_consolidation_worker_for_tests() -> None:
    shutdown_consolidation_worker()
    global _JOB_COUNTER
    with _JOB_COUNTER_LOCK:
        _JOB_COUNTER = 0

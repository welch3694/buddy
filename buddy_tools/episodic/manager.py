"""Episodic session lifecycle: open, idle close, max-duration split, shutdown."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from buddy_tools.episodic.config import EpisodicConfig, load_episodic_config
from buddy_tools.episodic.paths import (
    bucket_keys,
    ensure_session_directories,
    episodic_root,
    session_id_for,
    session_json_path,
)
from buddy_tools.episodic.rollup import register_session_in_rollups
from buddy_tools.episodic.session import (
    EpisodicSession,
    find_session_json_files,
    load_session,
    save_session,
    write_turns_placeholder,
)
from buddy_tools.episodic.turns import EpisodicTurnRecord, append_turn

logger = logging.getLogger(__name__)

_AGENT_BUSY_POLL_SECONDS = 30.0


class EpisodicSessionManager:
    """Manages one open episodic session per persona namespace."""

    def __init__(
        self,
        memory_root: Path,
        persona_namespace: str,
        config: EpisodicConfig | None = None,
        *,
        agent_busy_fn: Callable[[], bool] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.memory_root = memory_root.resolve()
        self.persona_namespace = persona_namespace
        self.config = config or load_episodic_config()
        self._agent_busy_fn = agent_busy_fn or (lambda: False)
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._session: EpisodicSession | None = None
        self._bucket: tuple[str, str, str] | None = None
        self._session_dir: Path | None = None
        self._next_seq: int = 0
        self._idle_timer: threading.Timer | None = None
        self._recover_orphan_sessions()

    def set_agent_busy_fn(self, fn: Callable[[], bool]) -> None:
        self._agent_busy_fn = fn

    def current_session(self) -> EpisodicSession | None:
        with self._lock:
            return self._session

    def on_user_activity(self, channel: str) -> EpisodicSession:
        """Open or continue the active session; reset idle timer on user activity."""
        with self._lock:
            channel = channel.strip()
            if self._session is not None and self._session.status == "open":
                if channel and channel not in self._session.channels:
                    self._session.channels.append(channel)
                    self._persist_session()
                if self._session_exceeded_max_duration():
                    self._close_current_session("max_duration")
                    return self._open_new_session(channel)
                self._reset_idle_timer()
                return self._session

            session = self._open_new_session(channel)
            self._reset_idle_timer()
            return session

    def close_if_idle(self) -> bool:
        """Close the open session when idle timeout elapsed and agent is quiescent."""
        with self._lock:
            if self._session is None or self._session.status != "open":
                return False
            if self._agent_busy_fn():
                logger.debug("Deferring episodic session close — agent busy")
                self._arm_idle_timer(_AGENT_BUSY_POLL_SECONDS)
                return False
            return self._close_current_session("idle_timeout")

    def force_close(self, idle_reason: str) -> bool:
        """Idempotent close for shutdown and external lifecycle hooks."""
        with self._lock:
            self._cancel_idle_timer()
            if self._session is None:
                return False
            if self._session.status == "closed":
                return False
            return self._close_current_session(idle_reason)

    def shutdown(self) -> bool:
        return self.force_close("shutdown")

    def close_for_personality_switch(self) -> bool:
        return self.force_close("personality_switch")

    def log_turn(self, record: EpisodicTurnRecord) -> None:
        """Append a turn record to the active session (thread-safe)."""
        with self._lock:
            if self._session is None or self._session_dir is None:
                logger.debug(
                    "Skipping episodic turn log (no open session): role=%r turn_id=%r",
                    record.role,
                    record.turn_id,
                )
                return
            if self._session.status != "open":
                logger.debug(
                    "Skipping episodic turn log (session not open): role=%r turn_id=%r",
                    record.role,
                    record.turn_id,
                )
                return

            self._next_seq += 1
            record.seq = self._next_seq
            append_turn(self._session_dir, self._session, record)

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _utc_now_iso(self) -> str:
        return self._now().replace(microsecond=0).isoformat()

    def _session_exceeded_max_duration(self) -> bool:
        assert self._session is not None
        started = _parse_iso(self._session.started_at)
        if started is None:
            return False
        elapsed = (self._now() - started).total_seconds()
        return elapsed >= self.config.max_session_seconds

    def _open_new_session(self, channel: str) -> EpisodicSession:
        now = self._now()
        tz = self.config.tzinfo
        year, year_month, year_month_day = bucket_keys(now, tz)
        session_id = session_id_for(now, tz)
        session_directory = ensure_session_directories(
            self.memory_root,
            self.persona_namespace,
            year,
            year_month,
            year_month_day,
            session_id,
        )
        channels = [channel] if channel else []
        session = EpisodicSession(
            session_id=session_id,
            status="open",
            started_at=self._utc_now_iso(),
            persona_namespace=self.persona_namespace,
            channels=channels,
        )
        self._session = session
        self._bucket = (year, year_month, year_month_day)
        self._session_dir = session_directory
        self._next_seq = 0
        save_session(session_json_path(session_directory), session)
        write_turns_placeholder(session_directory)
        register_session_in_rollups(
            self.memory_root,
            self.persona_namespace,
            year,
            year_month,
            year_month_day,
            session_id,
        )
        logger.info(
            "Opened episodic session %r for persona %r (channel=%r)",
            session_id,
            self.persona_namespace,
            channel or None,
        )
        return session

    def _close_current_session(self, idle_reason: str) -> bool:
        if self._session is None or self._session_dir is None:
            return False
        if self._session.status == "closed":
            return False

        session = self._session
        if session.status == "open":
            session.status = "closing"
            save_session(session_json_path(self._session_dir), session)

        session.status = "closed"
        session.idle_reason = idle_reason
        session.ended_at = self._utc_now_iso()
        save_session(session_json_path(self._session_dir), session)
        logger.info(
            "Closed episodic session %r for persona %r (reason=%r)",
            session.session_id,
            self.persona_namespace,
            idle_reason,
        )
        self._session = None
        self._bucket = None
        self._session_dir = None
        self._next_seq = 0
        return True

    def _persist_session(self) -> None:
        if self._session is None or self._session_dir is None:
            return
        save_session(session_json_path(self._session_dir), self._session)

    def _reset_idle_timer(self) -> None:
        self._arm_idle_timer(self.config.idle_timeout_seconds)

    def _arm_idle_timer(self, delay_seconds: float) -> None:
        self._cancel_idle_timer()
        timer = threading.Timer(delay_seconds, self._idle_timer_fired)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _idle_timer_fired(self) -> None:
        try:
            self.close_if_idle()
        except Exception:
            logger.exception("Episodic idle timer callback failed")

    def _recover_orphan_sessions(self) -> None:
        tree = episodic_root(self.memory_root, self.persona_namespace)
        for path in find_session_json_files(tree):
            session = load_session(path)
            if session is None:
                continue
            if session.status not in ("open", "closing"):
                continue
            logger.warning(
                "Recovering orphan episodic session %r (status=%r) — force closing",
                session.session_id,
                session.status,
            )
            session.status = "closed"
            session.idle_reason = "shutdown"
            session.ended_at = self._utc_now_iso()
            save_session(path, session)


def _parse_iso(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_MANAGER: EpisodicSessionManager | None = None
_SHOULD_LISTEN: Any = None
_EXECUTOR: Any = None


def configure_episodic(
    memory_root: Path,
    persona_namespace: str,
    *,
    should_listen: Any = None,
    config: EpisodicConfig | None = None,
) -> EpisodicSessionManager:
    """Create or replace the process-wide episodic session manager."""
    global _MANAGER, _SHOULD_LISTEN

    _SHOULD_LISTEN = should_listen
    _MANAGER = EpisodicSessionManager(
        memory_root,
        persona_namespace,
        config=config,
        agent_busy_fn=_default_agent_busy,
    )
    _link_executor_if_ready()
    return _MANAGER


def link_episodic_executor(executor: Any) -> None:
    """Attach the live tool executor for agent-busy detection."""
    global _EXECUTOR
    _EXECUTOR = executor
    _link_executor_if_ready()


def _link_executor_if_ready() -> None:
    if _MANAGER is not None:
        _MANAGER.set_agent_busy_fn(_default_agent_busy)


def get_episodic_manager() -> EpisodicSessionManager | None:
    return _MANAGER


def reconfigure_episodic_persona(persona_namespace: str) -> EpisodicSessionManager | None:
    """Rebind episodic storage to a new persona namespace (e.g. after personality switch)."""
    global _MANAGER

    if _MANAGER is None:
        return None

    namespace = persona_namespace.strip()
    if not namespace:
        return _MANAGER

    if _MANAGER.persona_namespace == namespace:
        return _MANAGER

    memory_root = _MANAGER.memory_root
    config = _MANAGER.config
    _MANAGER._cancel_idle_timer()
    _MANAGER = EpisodicSessionManager(
        memory_root,
        namespace,
        config=config,
        agent_busy_fn=_default_agent_busy,
    )
    _link_executor_if_ready()
    return _MANAGER


def reset_episodic_for_tests() -> None:
    global _MANAGER, _SHOULD_LISTEN, _EXECUTOR
    if _MANAGER is not None:
        _MANAGER._cancel_idle_timer()
    _MANAGER = None
    _SHOULD_LISTEN = None
    _EXECUTOR = None


def _default_agent_busy() -> bool:
    from buddy_tools.voice.listening_pause import get_listening_pause_controller

    if get_listening_pause_controller().paused:
        return True
    if _SHOULD_LISTEN is not None and not _SHOULD_LISTEN.is_set():
        return True
    if _EXECUTOR is not None:
        if getattr(_EXECUTOR, "_pending_tools", None):
            return True
        if getattr(_EXECUTOR, "_pending_context", None) is not None:
            return True
    return False

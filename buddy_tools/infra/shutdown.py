"""Process shutdown hooks for Buddy (episodic sessions, timers, etc.)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_shutdown_done = False


def finalize_buddy_session() -> None:
    """Idempotent cleanup when the speech-to-speech process exits."""
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True

    from buddy_tools.timers import cancel_all_timers

    cancel_all_timers()

    from buddy_tools.episodic import get_episodic_manager
    from buddy_tools.episodic.worker import shutdown_consolidation_worker

    manager = get_episodic_manager()
    if manager is not None:
        if manager.force_close("shutdown"):
            logger.info("Closed episodic session on shutdown")
        else:
            logger.debug("No open episodic session to close on shutdown")

    shutdown_consolidation_worker()


def reset_shutdown_state_for_tests() -> None:
    global _shutdown_done
    _shutdown_done = False

"""Episodic memory — per-persona session storage and lifecycle (Phase 1).

Conversation turns are stored under ``{BUDDY_DATA_DIR}/memory/{persona}/episodic/…``.
Paths are operator-visible on disk; no privacy UX in v1.
"""

from buddy_tools.episodic.config import (
    EpisodicConfig,
    load_episodic_config,
    reset_episodic_config_for_tests,
)
from buddy_tools.episodic.manager import (
    EpisodicSessionManager,
    configure_episodic,
    get_episodic_manager,
    link_episodic_executor,
    reconfigure_episodic_persona,
    reset_episodic_for_tests,
)
from buddy_tools.episodic.session import EpisodicSession
from buddy_tools.episodic.turns import EpisodicTurnRecord, append_turn, load_turns

__all__ = [
    "EpisodicConfig",
    "EpisodicSession",
    "EpisodicSessionManager",
    "EpisodicTurnRecord",
    "append_turn",
    "configure_episodic",
    "get_episodic_manager",
    "link_episodic_executor",
    "load_episodic_config",
    "load_turns",
    "reconfigure_episodic_persona",
    "reset_episodic_config_for_tests",
    "reset_episodic_for_tests",
]

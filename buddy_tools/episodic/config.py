"""Episodic memory configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_MINUTES = 20
DEFAULT_MAX_SESSION_MINUTES = 120
DEFAULT_TIMEZONE = "America/New_York"

_ENV_IDLE_TIMEOUT = "BUDDY_EPISODIC_IDLE_TIMEOUT_MINUTES"
_ENV_MAX_SESSION = "BUDDY_EPISODIC_MAX_SESSION_MINUTES"
_ENV_TIMEZONE = "BUDDY_EPISODIC_TIMEZONE"

_CACHED: EpisodicConfig | None = None


@dataclass(frozen=True)
class EpisodicConfig:
    idle_timeout_minutes: int
    max_session_minutes: int
    timezone: str

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def idle_timeout_seconds(self) -> float:
        return float(self.idle_timeout_minutes * 60)

    @property
    def max_session_seconds(self) -> float:
        return float(self.max_session_minutes * 60)


def _parse_positive_int(raw: str, *, name: str, default: int) -> int:
    text = raw.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s must be positive; got %d, using default %d", name, value, default)
        return default
    return value


def _parse_timezone(raw: str) -> str:
    text = raw.strip()
    if not text:
        return DEFAULT_TIMEZONE
    try:
        ZoneInfo(text)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid %s=%r; using default %r", _ENV_TIMEZONE, raw, DEFAULT_TIMEZONE)
        return DEFAULT_TIMEZONE
    return text


def load_episodic_config(*, force: bool = False) -> EpisodicConfig:
    """Load episodic config from environment (cached after first call)."""
    global _CACHED
    if _CACHED is not None and not force:
        return _CACHED

    config = EpisodicConfig(
        idle_timeout_minutes=_parse_positive_int(
            os.environ.get(_ENV_IDLE_TIMEOUT, ""),
            name=_ENV_IDLE_TIMEOUT,
            default=DEFAULT_IDLE_TIMEOUT_MINUTES,
        ),
        max_session_minutes=_parse_positive_int(
            os.environ.get(_ENV_MAX_SESSION, ""),
            name=_ENV_MAX_SESSION,
            default=DEFAULT_MAX_SESSION_MINUTES,
        ),
        timezone=_parse_timezone(os.environ.get(_ENV_TIMEZONE, "")),
    )
    _CACHED = config
    return config


def reset_episodic_config_for_tests() -> None:
    """Reset cached config for tests."""
    global _CACHED
    _CACHED = None

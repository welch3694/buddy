"""Load optional .env file from the repo root before reading configuration."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LOADED = False


def get_repo_root() -> Path:
    return _REPO_ROOT


def load_env_file(*, force: bool = False) -> Path | None:
    """Load `.env` from the repo root when present.

    Existing process environment variables are never overwritten.
    Returns the path loaded, or None if no file or python-dotenv is missing.
    """
    global _LOADED
    if _LOADED and not force:
        return None

    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        _LOADED = True
        return None

    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning(
            "Found %s but python-dotenv is not installed; run pip install python-dotenv",
            env_path,
        )
        _LOADED = True
        return None

    load_dotenv(env_path, override=False)
    _LOADED = True
    logger.info("Loaded environment from %s", env_path)
    return env_path


def reset_env_file_state() -> None:
    """Reset load state for tests."""
    global _LOADED
    _LOADED = False

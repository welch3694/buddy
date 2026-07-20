"""Multi-channel reply routing (voice, Telegram, etc.)."""

from __future__ import annotations

from typing import Any

__all__ = ["TelegramBridge", "get_telegram_bridge"]


def __getattr__(name: str) -> Any:
    # Lazy exports avoid circular import: registry → channels.tools → __init__ → telegram → data_dir
    if name in __all__:
        from buddy_tools.channels import telegram as _telegram

        return getattr(_telegram, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

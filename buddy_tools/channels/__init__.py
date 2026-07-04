"""Multi-channel reply routing (voice, Telegram, etc.)."""

from buddy_tools.channels.telegram import TelegramBridge, get_telegram_bridge

__all__ = ["TelegramBridge", "get_telegram_bridge"]

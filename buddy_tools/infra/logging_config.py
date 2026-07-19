"""Operator-facing logging tweaks for the voice pipeline."""

from __future__ import annotations

import logging

# httpx logs every request at INFO, including Telegram long-poll getUpdates with no messages.
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore")


def quiet_http_client_loggers(level: int = logging.WARNING) -> None:
    """Raise HTTP client log levels so empty Telegram polls do not flood the terminal.

    Call before speech-to-speech ``setup_logger`` / ``basicConfig``. Explicit child
    logger levels are preserved when the root logger is later set to INFO.
    """
    for name in _NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(level)

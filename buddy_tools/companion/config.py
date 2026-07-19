"""Opt-in localhost companion status bridge configuration (#115)."""

from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_ENABLED = "BUDDY_COMPANION_BRIDGE"
_ENV_HOST = "BUDDY_COMPANION_BRIDGE_HOST"
_ENV_PORT = "BUDDY_COMPANION_BRIDGE_PORT"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766


def _env_truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CompanionBridgeConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


def load_companion_bridge_config() -> CompanionBridgeConfig | None:
    """Return config when ``BUDDY_COMPANION_BRIDGE`` is enabled; otherwise None."""
    if not _env_truthy(os.environ.get(_ENV_ENABLED)):
        return None

    host = os.environ.get(_ENV_HOST, DEFAULT_HOST).strip() or DEFAULT_HOST
    port_raw = os.environ.get(_ENV_PORT, str(DEFAULT_PORT)).strip() or str(DEFAULT_PORT)
    try:
        port = int(port_raw)
    except ValueError:
        port = DEFAULT_PORT
    if port <= 0 or port > 65535:
        port = DEFAULT_PORT

    return CompanionBridgeConfig(host=host, port=port)

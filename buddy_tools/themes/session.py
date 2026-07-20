"""Apply theme switches and broadcast to the companion bridge (#138)."""

from __future__ import annotations

import logging

from buddy_tools.themes.catalog import ThemePack, get_theme, set_active_theme

logger = logging.getLogger(__name__)


def apply_theme(theme_id: str) -> ThemePack:
    """Persist the active theme and emit a companion ``theme`` event."""
    pack = set_active_theme(theme_id)
    _emit_theme_pack(pack)
    logger.info("Switched companion theme to %r", pack.id)
    return pack


def emit_active_theme() -> ThemePack | None:
    """Emit the currently active theme (bridge start). Returns pack or None."""
    try:
        from buddy_tools.themes.catalog import get_active_theme

        pack = get_active_theme()
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Could not load active theme for companion bridge: %s", exc)
        return None
    _emit_theme_pack(pack)
    return pack


def emit_theme_for_id(theme_id: str) -> ThemePack:
    """Validate and emit without changing active.json (tests / preview)."""
    pack = get_theme(theme_id)
    _emit_theme_pack(pack)
    return pack


def _emit_theme_pack(pack: ThemePack) -> None:
    from buddy_tools.companion.bridge import get_companion_bridge
    from buddy_tools.companion.publisher import emit_theme

    bridge = get_companion_bridge()
    tokens = pack.to_css_tokens()
    if bridge is not None and hasattr(bridge, "set_active_theme"):
        bridge.set_active_theme(theme_id=pack.id, name=pack.name, tokens=tokens)
        return
    emit_theme(theme_id=pack.id, name=pack.name, tokens=tokens)

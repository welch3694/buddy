"""Theme pack discovery and active theme persistence (#138)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from buddy_tools.themes.schema import (
    THEME_FILENAME,
    ThemePack,
    ThemeValidationError,
    is_valid_theme_dir,
    load_theme_yaml,
    sanitize_theme_id,
)

logger = logging.getLogger(__name__)

DEFAULT_THEME_ID = "default"
ACTIVE_FILENAME = "active.json"

_THEMES_DIR = Path(__file__).resolve().parent.parent.parent / "themes"


def get_themes_dir() -> Path:
    return _THEMES_DIR


def set_themes_dir(path: Path) -> None:
    global _THEMES_DIR
    _THEMES_DIR = path.resolve()


def _themes_root(themes_dir: Path | None = None) -> Path:
    return (themes_dir or _THEMES_DIR).resolve()


def _active_file(themes_dir: Path | None = None) -> Path:
    return _themes_root(themes_dir) / ACTIVE_FILENAME


def _theme_dir(themes_root: Path, theme_id: str) -> Path:
    sanitized = sanitize_theme_id(theme_id)
    path = (themes_root / sanitized).resolve()
    if path.parent != themes_root:
        raise ThemeValidationError(f"Invalid theme id: {theme_id!r}")
    return path


def list_themes(themes_dir: Path | None = None) -> list[dict[str, str]]:
    """Return ``[{id, name}, ...]`` for valid theme packs, sorted by id."""
    root = _themes_root(themes_dir)
    if not root.is_dir():
        return []
    entries: list[dict[str, str]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            pack = get_theme(entry.name, root)
        except (FileNotFoundError, ThemeValidationError):
            continue
        entries.append({"id": pack.id, "name": pack.name})
    return entries


def list_theme_ids(themes_dir: Path | None = None) -> list[str]:
    return [entry["id"] for entry in list_themes(themes_dir)]


def get_theme(theme_id: str, themes_dir: Path | None = None) -> ThemePack:
    root = _themes_root(themes_dir)
    sanitized = sanitize_theme_id(theme_id)
    directory = _theme_dir(root, sanitized)
    theme_path = directory / THEME_FILENAME

    if not directory.is_dir():
        raise FileNotFoundError(f"Theme {sanitized!r} not found under {root}")
    if not theme_path.is_file():
        raise FileNotFoundError(f"Theme {sanitized!r} missing {THEME_FILENAME} in {directory}")

    return load_theme_yaml(theme_path, expected_id=sanitized)


def get_active_theme_id(themes_dir: Path | None = None) -> str:
    active_path = _active_file(themes_dir)
    if not active_path.is_file():
        return DEFAULT_THEME_ID

    try:
        data = json.loads(active_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_THEME_ID

    if not isinstance(data, dict):
        return DEFAULT_THEME_ID

    raw_id = data.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return DEFAULT_THEME_ID

    try:
        return sanitize_theme_id(raw_id)
    except ThemeValidationError:
        return DEFAULT_THEME_ID


def set_active_theme(theme_id: str, themes_dir: Path | None = None) -> ThemePack:
    """Persist active theme id after validating the pack exists."""
    root = _themes_root(themes_dir)
    root.mkdir(parents=True, exist_ok=True)
    pack = get_theme(theme_id, root)
    active_path = _active_file(root)
    active_path.write_text(json.dumps({"id": pack.id}, indent=2) + "\n", encoding="utf-8")
    return pack


def get_active_theme(themes_dir: Path | None = None) -> ThemePack:
    """Load the active theme, falling back to default when missing/invalid."""
    root = _themes_root(themes_dir)
    active_id = get_active_theme_id(root)
    try:
        return get_theme(active_id, root)
    except (FileNotFoundError, ThemeValidationError) as exc:
        logger.warning("Active theme %r unavailable (%s); falling back to %s", active_id, exc, DEFAULT_THEME_ID)
        if active_id != DEFAULT_THEME_ID:
            try:
                return get_theme(DEFAULT_THEME_ID, root)
            except (FileNotFoundError, ThemeValidationError):
                pass
        raise


def ensure_theme_dir_valid(path: Path) -> bool:
    return is_valid_theme_dir(path)

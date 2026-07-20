"""Companion display themes from the user data directory (#138)."""

from buddy_tools.themes.catalog import (
    ACTIVE_FILENAME,
    DEFAULT_THEME_ID,
    get_active_theme,
    get_active_theme_id,
    get_theme,
    get_themes_dir,
    list_theme_ids,
    list_themes,
    set_active_theme,
    set_themes_dir,
)
from buddy_tools.themes.schema import (
    THEME_FILENAME,
    ThemePack,
    ThemeValidationError,
    is_valid_theme_dir,
)
from buddy_tools.themes.session import apply_theme, emit_active_theme
from buddy_tools.themes.tools import (
    THEME_TOOL_GROUP,
    THEME_TOOL_NAMES,
    execute_theme_tool,
)

__all__ = [
    "ACTIVE_FILENAME",
    "DEFAULT_THEME_ID",
    "THEME_FILENAME",
    "THEME_TOOL_GROUP",
    "THEME_TOOL_NAMES",
    "ThemePack",
    "ThemeValidationError",
    "apply_theme",
    "emit_active_theme",
    "execute_theme_tool",
    "get_active_theme",
    "get_active_theme_id",
    "get_theme",
    "get_themes_dir",
    "is_valid_theme_dir",
    "list_theme_ids",
    "list_themes",
    "set_active_theme",
    "set_themes_dir",
]

"""User data directory: memory, personalities, and startup seeding from repo templates."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path

from buddy_tools.infra.bootstrap import set_memory_root
from buddy_tools.personality import (
    ACTIVE_FILENAME,
    PROFILE_FILENAME,
    PROMPT_FILENAME,
    set_personalities_dir,
)
from buddy_tools.themes.catalog import set_themes_dir
from buddy_tools.themes.schema import THEME_FILENAME, is_valid_theme_dir
from buddy_tools.voice.voices import is_valid_voice_dir, set_voices_dir

logger = logging.getLogger(__name__)

_ENV_DATA_DIR = "BUDDY_DATA_DIR"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR: Path | None = None
_CONFIGURED = False
_TEST_REPO_ROOT: Path | None = None
_TEST_DATA_DIR: Path | None = None


def get_repo_root() -> Path:
    return (_TEST_REPO_ROOT or _REPO_ROOT).resolve()


def get_shipped_personalities_dir() -> Path:
    return get_repo_root() / "personalities"


def get_shipped_voices_dir() -> Path:
    return get_repo_root() / "voices"


def get_shipped_themes_dir() -> Path:
    return get_repo_root() / "themes"


def get_built_in_skills_dir() -> Path:
    return get_repo_root() / "skills"


def get_user_skills_dir() -> Path:
    return get_data_dir() / "skills"


def get_user_voices_dir() -> Path:
    return get_data_dir() / "voices"


def get_user_themes_dir() -> Path:
    return get_data_dir() / "themes"


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Buddy"
        return Path.home() / "AppData" / "Local" / "Buddy"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Buddy"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "buddy"
    return Path.home() / ".local" / "share" / "buddy"


def resolve_data_dir() -> Path:
    if _TEST_DATA_DIR is not None:
        return _TEST_DATA_DIR.resolve()
    override = os.environ.get(_ENV_DATA_DIR, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_data_dir().resolve()


def get_data_dir() -> Path:
    if _DATA_DIR is None:
        return resolve_data_dir()
    return _DATA_DIR


def _is_valid_personality_dir(path: Path) -> bool:
    return (path / PROFILE_FILENAME).is_file() and (path / PROMPT_FILENAME).is_file()


def _is_valid_theme_pack_dir(path: Path) -> bool:
    return (path / THEME_FILENAME).is_file() and is_valid_theme_dir(path)


def _memory_dir_has_content(memory_dir: Path) -> bool:
    if not memory_dir.is_dir():
        return False
    return any(memory_dir.rglob("*"))


def _voices_dir_has_content(voices_dir: Path) -> bool:
    if not voices_dir.is_dir():
        return False
    return any(entry.is_dir() and is_valid_voice_dir(entry) for entry in voices_dir.iterdir())


def _copy_tree_contents(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file():
        path.unlink()
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
    shutil.rmtree(path)


def seed_shipped_personalities(shipped_dir: Path, user_dir: Path) -> list[str]:
    """Copy shipped persona templates into user_dir when missing or incomplete."""
    if not shipped_dir.is_dir():
        return []

    user_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []

    for entry in sorted(shipped_dir.iterdir()):
        if not entry.is_dir() or not _SAFE_NAME.match(entry.name):
            continue
        if not _is_valid_personality_dir(entry):
            continue

        target = user_dir / entry.name
        if _is_valid_personality_dir(target):
            continue

        if target.exists():
            _remove_tree(target)

        shutil.copytree(entry, target)
        seeded.append(entry.name)
        logger.info("Seeded personality %r from %s", entry.name, entry)

    return seeded


def seed_shipped_voices(shipped_dir: Path, user_dir: Path) -> list[str]:
    """Copy shipped voice clones into user_dir when missing or incomplete."""
    if not shipped_dir.is_dir():
        return []

    user_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []

    for entry in sorted(shipped_dir.iterdir()):
        if not entry.is_dir() or not _SAFE_NAME.match(entry.name):
            continue
        if not is_valid_voice_dir(entry):
            continue

        target = user_dir / entry.name
        if is_valid_voice_dir(target):
            continue

        if target.exists():
            _remove_tree(target)

        shutil.copytree(entry, target)
        seeded.append(entry.name)
        logger.info("Seeded voice %r from %s", entry.name, entry)

    return seeded


def seed_shipped_themes(shipped_dir: Path, user_dir: Path) -> list[str]:
    """Copy shipped theme packs into user_dir when missing or incomplete."""
    if not shipped_dir.is_dir():
        return []

    user_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []

    for entry in sorted(shipped_dir.iterdir()):
        if not entry.is_dir() or not _SAFE_NAME.match(entry.name):
            continue
        if not _is_valid_theme_pack_dir(entry):
            continue

        target = user_dir / entry.name
        if _is_valid_theme_pack_dir(target):
            continue

        if target.exists():
            _remove_tree(target)

        shutil.copytree(entry, target)
        seeded.append(entry.name)
        logger.info("Seeded theme %r from %s", entry.name, entry)

    return seeded


def migrate_legacy_user_data(repo_root: Path, data_dir: Path) -> list[str]:
    """Copy legacy repo memory and active.json into the user data dir when appropriate."""
    actions: list[str] = []
    repo_memory = repo_root / "memory"
    user_memory = data_dir / "memory"
    user_personalities = data_dir / "personalities"

    if _memory_dir_has_content(repo_memory) and not _memory_dir_has_content(user_memory):
        _copy_tree_contents(repo_memory, user_memory)
        actions.append(f"memory from {repo_memory} to {user_memory}")

    legacy_active = repo_root / "personalities" / ACTIVE_FILENAME
    user_active = user_personalities / ACTIVE_FILENAME
    if legacy_active.is_file() and not user_active.is_file():
        user_personalities.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_active, user_active)
        actions.append(f"active.json from {legacy_active} to {user_active}")

    repo_voices = repo_root / "voices"
    user_voices = data_dir / "voices"
    if _voices_dir_has_content(repo_voices) and not _voices_dir_has_content(user_voices):
        _copy_tree_contents(repo_voices, user_voices)
        actions.append(f"voices from {repo_voices} to {user_voices}")

    for action in actions:
        logger.info("Migrated legacy user data: %s", action)

    return actions


def configure_user_data() -> Path:
    """Resolve paths, migrate legacy data, seed templates, and wire runtime roots."""
    global _DATA_DIR, _CONFIGURED

    data_dir = resolve_data_dir()
    repo_root = get_repo_root()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "memory").mkdir(parents=True, exist_ok=True)
    (data_dir / "personalities").mkdir(parents=True, exist_ok=True)
    (data_dir / "skills").mkdir(parents=True, exist_ok=True)
    (data_dir / "voices").mkdir(parents=True, exist_ok=True)
    (data_dir / "themes").mkdir(parents=True, exist_ok=True)

    migrate_legacy_user_data(repo_root, data_dir)
    seeded_personalities = seed_shipped_personalities(
        get_shipped_personalities_dir(), data_dir / "personalities"
    )
    seeded_voices = seed_shipped_voices(get_shipped_voices_dir(), data_dir / "voices")
    seeded_themes = seed_shipped_themes(get_shipped_themes_dir(), data_dir / "themes")

    set_memory_root(data_dir / "memory")
    set_personalities_dir(data_dir / "personalities")
    set_voices_dir(data_dir / "voices")
    set_themes_dir(data_dir / "themes")

    _DATA_DIR = data_dir
    _CONFIGURED = True

    logger.info("Buddy data dir: %s", data_dir)
    if seeded_personalities:
        logger.info("Seeded personalities: %s", ", ".join(seeded_personalities))
    if seeded_voices:
        logger.info("Seeded voices: %s", ", ".join(seeded_voices))
    if seeded_themes:
        logger.info("Seeded themes: %s", ", ".join(seeded_themes))

    return data_dir


def is_configured() -> bool:
    return _CONFIGURED


def reset_data_dir_config(
    *,
    repo_root: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Reset module state for tests."""
    global _DATA_DIR, _CONFIGURED, _TEST_REPO_ROOT, _TEST_DATA_DIR

    _DATA_DIR = None
    _CONFIGURED = False
    _TEST_REPO_ROOT = repo_root.resolve() if repo_root is not None else None
    _TEST_DATA_DIR = data_dir.resolve() if data_dir is not None else None

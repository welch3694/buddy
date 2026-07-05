"""Named personality profiles (prompt + metadata) for the Buddy voice assistant."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from buddy_tools.voice.voices import DEFAULT_VOICE_ID, get_voice

DEFAULT_PERSONALITY_ID = "buddy"
PROFILE_FILENAME = "profile.yaml"
PROMPT_FILENAME = "prompt.md"
ACTIVE_FILENAME = "active.json"

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_PERSONALITIES_DIR = Path(__file__).resolve().parent.parent.parent / "personalities"


@dataclass(frozen=True)
class PersonalityProfile:
    id: str
    name: str
    description: str
    voice_id: str
    behaviors: dict[str, str]
    memory_namespace: str
    prompt: str
    directory: Path


def get_personalities_dir() -> Path:
    return _PERSONALITIES_DIR


def set_personalities_dir(path: Path) -> None:
    global _PERSONALITIES_DIR
    _PERSONALITIES_DIR = path.resolve()


def _sanitize_personality_id(personality_id: str) -> str:
    cleaned = personality_id.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    if not cleaned or not _SAFE_NAME.match(cleaned):
        raise ValueError(f"Invalid personality id: {personality_id!r}")
    return cleaned


def _personalities_root(personalities_dir: Path | None = None) -> Path:
    return (personalities_dir or _PERSONALITIES_DIR).resolve()


def _personality_dir(root: Path, personality_id: str) -> Path:
    sanitized = _sanitize_personality_id(personality_id)
    path = (root / sanitized).resolve()
    if path.parent != root:
        raise ValueError(f"Invalid personality id: {personality_id!r}")
    return path


def _active_file(personalities_dir: Path | None = None) -> Path:
    return _personalities_root(personalities_dir) / ACTIVE_FILENAME


def _is_valid_personality_dir(path: Path) -> bool:
    return (path / PROFILE_FILENAME).is_file() and (path / PROMPT_FILENAME).is_file()


def _load_profile_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid {PROFILE_FILENAME} in {path.parent}: expected a mapping")
    return data


def _normalize_behaviors(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("behaviors must be a mapping of string keys to string values")
    behaviors: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("behaviors must be a mapping of string keys to string values")
        behaviors[key] = value
    return behaviors


def _write_profile_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def list_personalities(personalities_dir: Path | None = None) -> list[str]:
    """Return ids of personalities that have both profile.yaml and prompt.md."""
    root = _personalities_root(personalities_dir)
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and _SAFE_NAME.match(entry.name) and _is_valid_personality_dir(entry)
    )


def get_personality(
    personality_id: str,
    personalities_dir: Path | None = None,
    *,
    validate_voice: bool = True,
) -> PersonalityProfile:
    """Load a personality profile from disk."""
    root = _personalities_root(personalities_dir)
    sanitized = _sanitize_personality_id(personality_id)
    directory = _personality_dir(root, sanitized)

    profile_path = directory / PROFILE_FILENAME
    prompt_path = directory / PROMPT_FILENAME

    if not directory.is_dir():
        raise FileNotFoundError(f"Personality {sanitized!r} not found under {root}")
    if not profile_path.is_file():
        raise FileNotFoundError(f"Personality {sanitized!r} missing {PROFILE_FILENAME} in {directory}")
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Personality {sanitized!r} missing {PROMPT_FILENAME} in {directory}")

    raw = _load_profile_yaml(profile_path)
    profile_id = str(raw.get("id", sanitized))
    if profile_id != sanitized:
        raise ValueError(
            f"Personality folder {sanitized!r} has mismatched id {profile_id!r} in {PROFILE_FILENAME}"
        )

    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError(f"Personality {sanitized!r} missing name in {PROFILE_FILENAME}")

    voice_id = str(raw.get("voice_id", DEFAULT_VOICE_ID)).strip()
    if not voice_id:
        raise ValueError(f"Personality {sanitized!r} missing voice_id in {PROFILE_FILENAME}")

    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Personality {sanitized!r} has empty {PROMPT_FILENAME}")

    if validate_voice:
        get_voice(voice_id)

    return PersonalityProfile(
        id=sanitized,
        name=name,
        description=str(raw.get("description", "")).strip(),
        voice_id=voice_id,
        behaviors=_normalize_behaviors(raw.get("behaviors")),
        memory_namespace=str(raw.get("memory_namespace", sanitized)).strip() or sanitized,
        prompt=prompt,
        directory=directory.resolve(),
    )


def get_active_personality_id(personalities_dir: Path | None = None) -> str:
    """Read active personality id from active.json, falling back to buddy."""
    active_path = _active_file(personalities_dir)
    if not active_path.is_file():
        return DEFAULT_PERSONALITY_ID

    try:
        data = json.loads(active_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_PERSONALITY_ID

    if not isinstance(data, dict):
        return DEFAULT_PERSONALITY_ID

    raw_id = data.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return DEFAULT_PERSONALITY_ID

    try:
        return _sanitize_personality_id(raw_id)
    except ValueError:
        return DEFAULT_PERSONALITY_ID


def set_active_personality(personality_id: str, personalities_dir: Path | None = None) -> None:
    """Persist the active personality id to active.json."""
    root = _personalities_root(personalities_dir)
    root.mkdir(parents=True, exist_ok=True)
    sanitized = _sanitize_personality_id(personality_id)
    get_personality(sanitized, root)
    active_path = _active_file(root)
    active_path.write_text(json.dumps({"id": sanitized}, indent=2) + "\n", encoding="utf-8")


def get_active_personality(
    personalities_dir: Path | None = None,
    *,
    validate_voice: bool = True,
) -> PersonalityProfile:
    """Load the active personality, falling back to buddy if needed."""
    root = _personalities_root(personalities_dir)
    active_id = get_active_personality_id(root)

    try:
        return get_personality(active_id, root, validate_voice=validate_voice)
    except (FileNotFoundError, ValueError):
        if active_id != DEFAULT_PERSONALITY_ID:
            return get_personality(DEFAULT_PERSONALITY_ID, root, validate_voice=validate_voice)
        raise


def create_personality(
    personality_id: str,
    name: str,
    prompt: str,
    *,
    description: str = "",
    voice_id: str = DEFAULT_VOICE_ID,
    behaviors: dict[str, str] | None = None,
    memory_namespace: str | None = None,
    personalities_dir: Path | None = None,
    validate_voice: bool = True,
) -> PersonalityProfile:
    """Create a new personality folder with profile.yaml and prompt.md."""
    root = _personalities_root(personalities_dir)
    sanitized = _sanitize_personality_id(personality_id)
    directory = _personality_dir(root, sanitized)

    if directory.exists():
        raise FileExistsError(f"Personality {sanitized!r} already exists")

    prompt_text = prompt.strip()
    if not prompt_text:
        raise ValueError("prompt cannot be empty")

    name_text = name.strip()
    if not name_text:
        raise ValueError("name cannot be empty")

    if validate_voice:
        get_voice(voice_id)

    namespace = (memory_namespace or sanitized).strip() or sanitized
    profile_data = {
        "id": sanitized,
        "name": name_text,
        "description": description.strip(),
        "voice_id": voice_id,
        "behaviors": behaviors or {},
        "memory_namespace": namespace,
    }

    directory.mkdir(parents=True, exist_ok=False)
    _write_profile_yaml(directory / PROFILE_FILENAME, profile_data)
    (directory / PROMPT_FILENAME).write_text(prompt_text.rstrip() + "\n", encoding="utf-8")
    return get_personality(sanitized, root, validate_voice=validate_voice)


def update_personality(
    personality_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    prompt: str | None = None,
    voice_id: str | None = None,
    behaviors: dict[str, str] | None = None,
    memory_namespace: str | None = None,
    personalities_dir: Path | None = None,
    validate_voice: bool = True,
) -> PersonalityProfile:
    """Update fields on an existing personality."""
    root = _personalities_root(personalities_dir)
    existing = get_personality(personality_id, root, validate_voice=False)
    profile_path = existing.directory / PROFILE_FILENAME

    raw = _load_profile_yaml(profile_path)

    if name is not None:
        name_text = name.strip()
        if not name_text:
            raise ValueError("name cannot be empty")
        raw["name"] = name_text

    if description is not None:
        raw["description"] = description.strip()

    if voice_id is not None:
        voice_text = voice_id.strip()
        if not voice_text:
            raise ValueError("voice_id cannot be empty")
        if validate_voice:
            get_voice(voice_text)
        raw["voice_id"] = voice_text

    if behaviors is not None:
        raw["behaviors"] = _normalize_behaviors(behaviors)

    if memory_namespace is not None:
        namespace = memory_namespace.strip()
        if not namespace:
            raise ValueError("memory_namespace cannot be empty")
        raw["memory_namespace"] = namespace

    _write_profile_yaml(profile_path, raw)

    if prompt is not None:
        prompt_text = prompt.strip()
        if not prompt_text:
            raise ValueError("prompt cannot be empty")
        (existing.directory / PROMPT_FILENAME).write_text(prompt_text.rstrip() + "\n", encoding="utf-8")

    return get_personality(existing.id, root, validate_voice=validate_voice)


def delete_personality(personality_id: str, personalities_dir: Path | None = None) -> None:
    """Delete a personality folder from the user data dir.

    Shipped templates (e.g. buddy) are re-seeded from the repo on next startup when missing.
    """
    sanitized = _sanitize_personality_id(personality_id)
    root = _personalities_root(personalities_dir)
    directory = _personality_dir(root, sanitized)
    if not directory.is_dir():
        raise FileNotFoundError(f"Personality {sanitized!r} not found under {root}")

    active_id = get_active_personality_id(root)
    if active_id == sanitized:
        set_active_personality(DEFAULT_PERSONALITY_ID, root)

    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
    directory.rmdir()

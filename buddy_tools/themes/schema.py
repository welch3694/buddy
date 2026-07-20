"""theme.yaml schema validation and CSS-token flattening (#138)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

THEME_FILENAME = "theme.yaml"

PALETTE_KEYS = (
    "void",
    "void_mid",
    "teal",
    "cyan",
    "ice",
    "amber",
    "muted",
    "danger",
)
FONT_KEYS = ("display", "mono")
ORB_COLOR_KEYS = ("core", "glow", "ring")
ORB_NUMERIC_KEYS = (
    "scale",
    "breathe_amp",
    "breathe_ms",
    "bloom_opacity",
    "shimmer_opacity",
    "ring_spin_ms",
    "saturate",
)
ORB_KEYS = ORB_COLOR_KEYS + ORB_NUMERIC_KEYS
ORB_STATES = (
    "listening",
    "holding",
    "generating",
    "speaking",
    "paused",
    "offline",
)

# Base orb keys → CSS custom properties on :root (motion vars are unprefixed).
_ORB_BASE_CSS_NAMES: dict[str, str] = {
    "core": "--orb-core",
    "glow": "--orb-glow",
    "ring": "--orb-ring",
    "scale": "--orb-scale",
    "breathe_amp": "--breathe-amp",
    "breathe_ms": "--breathe-ms",
    "bloom_opacity": "--bloom-opacity",
    "shimmer_opacity": "--shimmer-opacity",
    "ring_spin_ms": "--ring-spin-ms",
    "saturate": "--saturate",
}

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(
    r"^rgba?\(\s*"
    r"(\d{1,3})\s*,\s*"
    r"(\d{1,3})\s*,\s*"
    r"(\d{1,3})"
    r"(?:\s*,\s*(0|1|0?\.\d+))?"
    r"\s*\)$"
)
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FORBIDDEN_CSS = re.compile(r"url\s*\(|expression\s*\(|@import|</?style", re.IGNORECASE)

# Numeric ranges for orb tokens.
_ORB_RANGES: dict[str, tuple[float, float]] = {
    "scale": (0.5, 2.0),
    "breathe_amp": (0.0, 0.5),
    "breathe_ms": (1.0, 60000.0),
    "bloom_opacity": (0.0, 1.0),
    "shimmer_opacity": (0.0, 1.0),
    "ring_spin_ms": (1.0, 300000.0),
    "saturate": (0.0, 3.0),
}


class ThemeValidationError(ValueError):
    """Raised when theme.yaml fails schema validation."""


@dataclass(frozen=True)
class ThemePack:
    id: str
    name: str
    palette: dict[str, str]
    fonts: dict[str, str]
    orb_base: dict[str, str | float]
    orb_states: dict[str, dict[str, str | float]] = field(default_factory=dict)
    directory: Path | None = None

    def to_css_tokens(self) -> dict[str, str]:
        """Flatten pack tokens into CSS custom-property name → value."""
        tokens: dict[str, str] = {}
        for key, value in self.palette.items():
            tokens[f"--{key.replace('_', '-')}"] = value
        for key, value in self.fonts.items():
            tokens[f"--font-{key}"] = value
        for key, value in self.orb_base.items():
            css_name = _ORB_BASE_CSS_NAMES[key]
            tokens[css_name] = _format_orb_css(key, value)
        for state, overrides in self.orb_states.items():
            for key, value in overrides.items():
                tokens[f"--orb-{state}-{key.replace('_', '-')}"] = _format_orb_css(key, value)
        return tokens


def _format_orb_css(key: str, value: str | float) -> str:
    if key in ("breathe_ms", "ring_spin_ms"):
        return f"{int(value)}ms" if float(value) == int(value) else f"{value}ms"
    if key in ORB_COLOR_KEYS:
        return str(value)
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _reject_forbidden(value: str, field_name: str) -> None:
    if _FORBIDDEN_CSS.search(value):
        raise ThemeValidationError(f"{field_name} contains forbidden CSS")


def _validate_color(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ThemeValidationError(f"{field_name} must be a color string")
    cleaned = value.strip()
    if not cleaned:
        raise ThemeValidationError(f"{field_name} must not be empty")
    _reject_forbidden(cleaned, field_name)
    if _HEX_RE.match(cleaned):
        return cleaned
    match = _RGB_RE.match(cleaned)
    if match:
        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if max(r, g, b) > 255:
            raise ThemeValidationError(f"{field_name} rgb channels must be 0-255")
        return cleaned
    raise ThemeValidationError(
        f"{field_name} must be #rgb, #rrggbb, rgb(...), or rgba(...)"
    )


def _validate_font(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ThemeValidationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ThemeValidationError(f"{field_name} must not be empty")
    _reject_forbidden(cleaned, field_name)
    if ";" in cleaned or "{" in cleaned or "}" in cleaned:
        raise ThemeValidationError(f"{field_name} contains invalid characters")
    return cleaned


def _validate_orb_numeric(key: str, value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThemeValidationError(f"{field_name} must be a number")
    numeric = float(value)
    low, high = _ORB_RANGES[key]
    if numeric < low or numeric > high:
        raise ThemeValidationError(f"{field_name} must be between {low} and {high}")
    return numeric


def _parse_orb_section(
    raw: Any,
    *,
    section_name: str,
    require_all: bool,
) -> dict[str, str | float]:
    if raw is None:
        if require_all:
            raise ThemeValidationError(f"{section_name} is required")
        return {}
    if not isinstance(raw, dict):
        raise ThemeValidationError(f"{section_name} must be a mapping")

    unknown = set(raw) - set(ORB_KEYS)
    if unknown:
        raise ThemeValidationError(
            f"{section_name} has unknown keys: {', '.join(sorted(unknown))}"
        )

    if require_all:
        missing = [key for key in ORB_KEYS if key not in raw]
        if missing:
            raise ThemeValidationError(
                f"{section_name} missing keys: {', '.join(missing)}"
            )

    parsed: dict[str, str | float] = {}
    for key, value in raw.items():
        field_name = f"{section_name}.{key}"
        if key in ORB_COLOR_KEYS:
            parsed[key] = _validate_color(value, field_name)
        else:
            parsed[key] = _validate_orb_numeric(key, value, field_name)
    return parsed


def sanitize_theme_id(theme_id: str) -> str:
    cleaned = theme_id.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    if not cleaned or not _SAFE_ID.match(cleaned):
        raise ThemeValidationError(f"Invalid theme id: {theme_id!r}")
    return cleaned


def parse_theme_dict(raw: Any, *, expected_id: str | None = None) -> ThemePack:
    if not isinstance(raw, dict):
        raise ThemeValidationError("theme.yaml root must be a mapping")

    unknown_top = set(raw) - {"id", "name", "palette", "fonts", "orb"}
    if unknown_top:
        raise ThemeValidationError(
            f"theme.yaml has unknown keys: {', '.join(sorted(unknown_top))}"
        )

    raw_id = raw.get("id")
    if not isinstance(raw_id, str):
        raise ThemeValidationError("id is required")
    theme_id = sanitize_theme_id(raw_id)
    if expected_id is not None and theme_id != expected_id:
        raise ThemeValidationError(
            f"theme id {theme_id!r} does not match directory {expected_id!r}"
        )

    raw_name = raw.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ThemeValidationError("name is required")
    name = raw_name.strip()
    _reject_forbidden(name, "name")

    palette_raw = raw.get("palette")
    if not isinstance(palette_raw, dict):
        raise ThemeValidationError("palette is required")
    unknown_palette = set(palette_raw) - set(PALETTE_KEYS)
    if unknown_palette:
        raise ThemeValidationError(
            f"palette has unknown keys: {', '.join(sorted(unknown_palette))}"
        )
    missing_palette = [key for key in PALETTE_KEYS if key not in palette_raw]
    if missing_palette:
        raise ThemeValidationError(
            f"palette missing keys: {', '.join(missing_palette)}"
        )
    palette = {
        key: _validate_color(palette_raw[key], f"palette.{key}") for key in PALETTE_KEYS
    }

    fonts_raw = raw.get("fonts")
    if not isinstance(fonts_raw, dict):
        raise ThemeValidationError("fonts is required")
    unknown_fonts = set(fonts_raw) - set(FONT_KEYS)
    if unknown_fonts:
        raise ThemeValidationError(
            f"fonts has unknown keys: {', '.join(sorted(unknown_fonts))}"
        )
    missing_fonts = [key for key in FONT_KEYS if key not in fonts_raw]
    if missing_fonts:
        raise ThemeValidationError(f"fonts missing keys: {', '.join(missing_fonts)}")
    fonts = {key: _validate_font(fonts_raw[key], f"fonts.{key}") for key in FONT_KEYS}

    orb_raw = raw.get("orb")
    if not isinstance(orb_raw, dict):
        raise ThemeValidationError("orb is required")
    unknown_orb = set(orb_raw) - {"base", "states"}
    if unknown_orb:
        raise ThemeValidationError(
            f"orb has unknown keys: {', '.join(sorted(unknown_orb))}"
        )

    orb_base = _parse_orb_section(orb_raw.get("base"), section_name="orb.base", require_all=True)

    states_raw = orb_raw.get("states")
    orb_states: dict[str, dict[str, str | float]] = {}
    if states_raw is not None:
        if not isinstance(states_raw, dict):
            raise ThemeValidationError("orb.states must be a mapping")
        unknown_states = set(states_raw) - set(ORB_STATES)
        if unknown_states:
            raise ThemeValidationError(
                f"orb.states has unknown keys: {', '.join(sorted(unknown_states))}"
            )
        for state in ORB_STATES:
            if state not in states_raw:
                continue
            orb_states[state] = _parse_orb_section(
                states_raw[state],
                section_name=f"orb.states.{state}",
                require_all=False,
            )

    return ThemePack(
        id=theme_id,
        name=name,
        palette=palette,
        fonts=fonts,
        orb_base=orb_base,
        orb_states=orb_states,
    )


def load_theme_yaml(path: Path, *, expected_id: str | None = None) -> ThemePack:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ThemeValidationError(f"could not read {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ThemeValidationError(f"invalid YAML in {path}: {exc}") from exc
    pack = parse_theme_dict(raw, expected_id=expected_id)
    return ThemePack(
        id=pack.id,
        name=pack.name,
        palette=pack.palette,
        fonts=pack.fonts,
        orb_base=pack.orb_base,
        orb_states=pack.orb_states,
        directory=path.parent.resolve(),
    )


def is_valid_theme_dir(path: Path) -> bool:
    theme_path = path / THEME_FILENAME
    if not theme_path.is_file():
        return False
    try:
        load_theme_yaml(theme_path, expected_id=path.name)
    except ThemeValidationError:
        return False
    return True

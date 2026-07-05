"""Local tools for the Buddy voice assistant."""

from __future__ import annotations

__all__ = ["apply_patches"]


def apply_patches() -> None:
    """Apply speech-to-speech monkey-patches (imported lazily to avoid side effects)."""
    from buddy_tools.core.patch import apply_patches as _apply_patches

    _apply_patches()

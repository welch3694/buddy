#!/usr/bin/env python
"""Launch speech-to-speech with local persistent memory tool execution."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is importable when launched from anywhere.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from buddy_tools import apply_patches
from buddy_tools.env import load_env_file


def main() -> None:
    load_env_file()
    apply_patches()
    from speech_to_speech.s2s_pipeline import main as s2s_main

    s2s_main()


if __name__ == "__main__":
    main()

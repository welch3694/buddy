"""Short-utterance discard gate for voice commits (#124).

Filters very short STT finals (grunts, noise, one-word fillers) before they
become chat turns. This is a **discard** gate — distinct from the turn-completion
heuristic, which only holds incomplete speech then still commits.

Environment variables:

- ``BUDDY_SHORT_UTTERANCE_GATE`` — set to ``0``/``false``/``off`` to disable (default on)
- ``BUDDY_SHORT_UTTERANCE_MIN_WORDS`` — min whitespace-separated words (default 2)
- ``BUDDY_SHORT_UTTERANCE_MIN_CHARS`` — min normalized char length; ``0`` disables (default 0)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from buddy_tools.voice.listening_pause import normalize_transcript

_ENV_GATE_ENABLED = "BUDDY_SHORT_UTTERANCE_GATE"
_ENV_MIN_WORDS = "BUDDY_SHORT_UTTERANCE_MIN_WORDS"
_ENV_MIN_CHARS = "BUDDY_SHORT_UTTERANCE_MIN_CHARS"

_DEFAULT_MIN_WORDS = 2
_DEFAULT_MIN_CHARS = 0

# Whole-utterance fillers / grunts only (not trailing tokens).
_FILLERS = frozenset(
    {
        "um",
        "uh",
        "uhh",
        "umm",
        "er",
        "erm",
        "hmm",
        "ah",
        "oh",
        "yeah",
        "yup",
        "nah",
        "mm",
        "mmm",
        "mhm",
        "ugh",
        "huh",
        "ahem",
    }
)

# Short meaningful replies that must survive min_words=2.
_SHORT_REPLY_ALLOWLIST = frozenset(
    {
        "yes",
        "no",
        "ok",
        "okay",
        "sure",
        "yep",
        "nope",
        "stop",
        "wait",
        "what",
        "thanks",
    }
)


class DiscardReason(str, Enum):
    EMPTY = "empty"
    FILLER = "filler"
    MIN_WORDS = "min_words"
    MIN_CHARS = "min_chars"


@dataclass(frozen=True)
class ShortUtteranceConfig:
    enabled: bool = True
    min_words: int = _DEFAULT_MIN_WORDS
    min_chars: int = _DEFAULT_MIN_CHARS
    extra_fillers: frozenset[str] = field(default_factory=frozenset)
    extra_allowlist: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls) -> ShortUtteranceConfig:
        return cls(
            enabled=_env_bool(_ENV_GATE_ENABLED, default=True),
            min_words=_env_int(_ENV_MIN_WORDS, _DEFAULT_MIN_WORDS),
            min_chars=_env_int(_ENV_MIN_CHARS, _DEFAULT_MIN_CHARS),
        )


_CACHED_CONFIG: ShortUtteranceConfig | None = None


def get_short_utterance_config() -> ShortUtteranceConfig:
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        _CACHED_CONFIG = ShortUtteranceConfig.from_env()
    return _CACHED_CONFIG


def reset_short_utterance_config_for_tests() -> None:
    global _CACHED_CONFIG
    _CACHED_CONFIG = None


def should_discard_utterance(
    transcript: str,
    *,
    config: ShortUtteranceConfig | None = None,
) -> DiscardReason | None:
    """Return a discard reason when the transcript should not commit to the LLM."""
    cfg = config if config is not None else get_short_utterance_config()
    if not cfg.enabled:
        return None

    normalized = normalize_transcript(transcript)
    if not normalized:
        return DiscardReason.EMPTY

    fillers = _FILLERS | cfg.extra_fillers
    if normalized in fillers:
        return DiscardReason.FILLER

    from buddy_tools.voice.action_intents import match_action_intent

    if match_action_intent(transcript) is not None:
        return None

    allowlist = _SHORT_REPLY_ALLOWLIST | cfg.extra_allowlist
    if normalized in allowlist:
        return None

    words = normalized.split()
    if len(words) < cfg.min_words:
        return DiscardReason.MIN_WORDS

    if cfg.min_chars > 0 and len(normalized) < cfg.min_chars:
        return DiscardReason.MIN_CHARS

    return None


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default

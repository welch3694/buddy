"""Tier-1 turn-completion heuristics for voice endpointing (#80).

After VAD soft-end and the endpointing gate clears speculative reopen grace,
these zero-latency rules detect obviously incomplete utterances and return
CONTINUE so the gate extends the hold instead of committing to the LLM.

Built-in CONTINUE patterns (trailing token, word-boundary match after normalization):

- **Disfluency suffixes:** um, uh, uhh, umm, er, erm, hmm
- **Dangling conjunctions / clause openers:** and, but, so, because, or, if, when, that, like
- **Punctuation cutoffs:** trailing `,`, `...`, `…`, `-`, `—`

Everything else returns UNKNOWN (feeds tier-3 LLM judge in #81). DONE is reserved
for future explicit complete-sentence signals; v1 does not emit DONE.

Environment variables:

- ``BUDDY_ENDPOINTING_HEURISTICS`` — set to ``0``/``false``/``off`` to disable (default on)
- ``BUDDY_ENDPOINTING_CONTINUE_HOLD_S`` — seconds to extend reopen grace on CONTINUE (default 2.5)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum

_ENV_HEURISTICS_ENABLED = "BUDDY_ENDPOINTING_HEURISTICS"
_ENV_CONTINUE_HOLD_S = "BUDDY_ENDPOINTING_CONTINUE_HOLD_S"

_DEFAULT_CONTINUE_HOLD_S = 2.5

_DISFLUENCY_SUFFIXES = frozenset({"um", "uh", "uhh", "umm", "er", "erm", "hmm"})
_DANGLING_CONJUNCTIONS = frozenset(
    {"and", "but", "so", "because", "or", "if", "when", "that", "like"}
)
_TRAILING_PUNCTUATION_RE = re.compile(r"[,\.\-—…]+$")
_ELLIPSIS_SUFFIX_RE = re.compile(r"\.{2,}$|…$")


class TurnCompletionVerdict(Enum):
    CONTINUE = "continue"
    UNKNOWN = "unknown"
    DONE = "done"


@dataclass(frozen=True)
class HeuristicConfig:
    enabled: bool = True
    continue_hold_s: float = _DEFAULT_CONTINUE_HOLD_S
    extra_disfluency_suffixes: frozenset[str] = field(default_factory=frozenset)
    extra_dangling_conjunctions: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls) -> HeuristicConfig:
        return cls(
            enabled=_env_bool(_ENV_HEURISTICS_ENABLED, default=True),
            continue_hold_s=_env_float(_ENV_CONTINUE_HOLD_S, _DEFAULT_CONTINUE_HOLD_S),
        )


_CACHED_CONFIG: HeuristicConfig | None = None


def get_heuristic_config() -> HeuristicConfig:
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        _CACHED_CONFIG = HeuristicConfig.from_env()
    return _CACHED_CONFIG


def reset_heuristic_config_for_tests() -> None:
    global _CACHED_CONFIG
    _CACHED_CONFIG = None


def classify_turn_completion_heuristic(
    transcript: str,
    *,
    config: HeuristicConfig | None = None,
) -> TurnCompletionVerdict:
    """Classify whether a soft-end transcript is obviously incomplete."""
    cfg = config if config is not None else get_heuristic_config()
    if not cfg.enabled:
        return TurnCompletionVerdict.UNKNOWN

    text = transcript.strip()
    if not text:
        return TurnCompletionVerdict.UNKNOWN

    if _has_trailing_punctuation_cutoff(text):
        return TurnCompletionVerdict.CONTINUE

    final_token = _final_token(text)
    if not final_token:
        return TurnCompletionVerdict.UNKNOWN

    disfluency = _DISFLUENCY_SUFFIXES | cfg.extra_disfluency_suffixes
    conjunctions = _DANGLING_CONJUNCTIONS | cfg.extra_dangling_conjunctions
    if final_token in disfluency or final_token in conjunctions:
        return TurnCompletionVerdict.CONTINUE

    return TurnCompletionVerdict.UNKNOWN


def _final_token(text: str) -> str:
    parts = text.split()
    if not parts:
        return ""
    token = parts[-1].lower()
    return _TRAILING_PUNCTUATION_RE.sub("", token)


def _has_trailing_punctuation_cutoff(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(","):
        return True
    if _ELLIPSIS_SUFFIX_RE.search(stripped):
        return True
    if stripped.endswith("-") or stripped.endswith("—"):
        return True
    return False


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default

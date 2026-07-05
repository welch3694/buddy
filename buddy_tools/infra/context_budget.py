"""Working-context token budget: preflight trim, observation masking, overflow recovery."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any

from openai.types.realtime.conversation_item import (
    RealtimeConversationItemAssistantMessage,
    RealtimeConversationItemFunctionCallOutput,
    RealtimeConversationItemUserMessage,
)

from speech_to_speech.LLM.chat import Chat

logger = logging.getLogger(__name__)

_MASKED_PREFIX = "[tool result hidden - "
_MASKED_SUFFIX = " chars]"

_OVERFLOW_PATTERNS = (
    re.compile(r"context", re.IGNORECASE),
    re.compile(r"n_ctx", re.IGNORECASE),
    re.compile(r"exceed", re.IGNORECASE),
    re.compile(r"too many tokens", re.IGNORECASE),
    re.compile(r"context_length_exceeded", re.IGNORECASE),
    re.compile(r"kv cache", re.IGNORECASE),
)


@dataclass(frozen=True)
class ContextBudget:
    """Configurable token budget derived from llama-server ctx-size."""

    ctx_size: int = 16384
    output_reserve: int = 1024
    safety_margin: int = 512
    mask_keep_recent_turns: int = 4
    image_tokens: int = 300
    chars_per_token: float = 4.0

    @property
    def effective_budget(self) -> int:
        return max(0, self.ctx_size - self.output_reserve - self.safety_margin)

    @classmethod
    def from_env(cls) -> ContextBudget:
        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Invalid %s=%r; using default %d", name, raw, default)
                return default

        def _float(name: str, default: float) -> float:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("Invalid %s=%r; using default %s", name, raw, default)
                return default

        return cls(
            ctx_size=_int("BUDDY_CTX_SIZE", 16384),
            output_reserve=_int("BUDDY_CTX_OUTPUT_RESERVE", 1024),
            safety_margin=_int("BUDDY_CTX_SAFETY_MARGIN", 512),
            mask_keep_recent_turns=_int("BUDDY_CTX_MASK_KEEP_TURNS", 4),
            image_tokens=_int("BUDDY_CTX_IMAGE_TOKENS", 300),
            chars_per_token=_float("BUDDY_CTX_CHARS_PER_TOKEN", 4.0),
        )


@dataclass
class TrimReport:
    """Summary of a preflight or recovery trim pass."""

    estimated_before: int = 0
    estimated_after: int = 0
    masked_outputs: int = 0
    evicted_turns: int = 0
    hard_reset: bool = False

    @property
    def acted(self) -> bool:
        return self.masked_outputs > 0 or self.evicted_turns > 0 or self.hard_reset


def estimate_tokens(text: str, *, chars_per_token: float = 4.0) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def estimate_tools_tokens(tools: list[Any] | None, *, chars_per_token: float = 4.0) -> int:
    if not tools:
        return 0
    try:
        serialized = json.dumps(
            [t.model_dump(exclude_none=True) if hasattr(t, "model_dump") else t for t in tools],
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        serialized = str(tools)
    return estimate_tokens(serialized, chars_per_token=chars_per_token)


def _buffer_text_tokens(chat: Chat, *, chars_per_token: float, image_tokens: int) -> int:
    total = 0
    for item in chat.buffer:
        if isinstance(item, RealtimeConversationItemUserMessage):
            for part in item.content:
                if part.type == "input_text" and part.text:
                    total += estimate_tokens(part.text, chars_per_token=chars_per_token)
                elif part.type == "input_image":
                    total += image_tokens
        elif isinstance(item, RealtimeConversationItemAssistantMessage):
            for part in item.content:
                if part.type == "output_text" and part.text:
                    total += estimate_tokens(part.text, chars_per_token=chars_per_token)
        elif hasattr(item, "output") and item.output is not None:
            total += estimate_tokens(str(item.output), chars_per_token=chars_per_token)
        elif hasattr(item, "arguments") and item.arguments is not None:
            total += estimate_tokens(str(item.arguments), chars_per_token=chars_per_token)
    return total


def estimate_chat_tokens(
    chat: Chat,
    instructions: str | None,
    tools: list[Any] | None,
    budget: ContextBudget | None = None,
) -> int:
    cfg = budget or ContextBudget.from_env()
    system_text = instructions or ""
    if chat.init_chat_message:
        system_text = " ".join(
            p.text for p in chat.init_chat_message.content if getattr(p, "text", None)
        ) or system_text
    total = estimate_tokens(system_text, chars_per_token=cfg.chars_per_token)
    total += estimate_tools_tokens(tools, chars_per_token=cfg.chars_per_token)
    total += _buffer_text_tokens(
        chat,
        chars_per_token=cfg.chars_per_token,
        image_tokens=cfg.image_tokens,
    )
    return total


def _user_turn_indices(chat: Chat) -> list[int]:
    return [
        i
        for i, item in enumerate(chat.buffer)
        if isinstance(item, RealtimeConversationItemUserMessage)
    ]


def _mask_placeholder(original_len: int) -> str:
    return f"{_MASKED_PREFIX}{original_len}{_MASKED_SUFFIX}"


def _is_already_masked(output: str) -> bool:
    return output.startswith(_MASKED_PREFIX) and output.endswith(_MASKED_SUFFIX)


def mask_old_tool_outputs(
    chat: Chat,
    *,
    keep_recent_turns: int,
    instructions: str | None = None,
    tools: list[Any] | None = None,
    budget: ContextBudget | None = None,
) -> int:
    """Replace verbose tool outputs outside the recency window with placeholders."""
    cfg = budget or ContextBudget.from_env()
    user_indices = _user_turn_indices(chat)
    if not user_indices:
        return 0

    total_turns = len(user_indices)
    keep_from_turn = max(0, total_turns - keep_recent_turns)
    if keep_from_turn <= 0:
        return 0

    first_recent_buffer_idx = user_indices[keep_from_turn] if keep_from_turn < total_turns else len(chat.buffer)
    masked = 0

    with chat._lock:
        for i, item in enumerate(chat.buffer):
            if i >= first_recent_buffer_idx:
                break
            if not isinstance(item, RealtimeConversationItemFunctionCallOutput):
                continue
            output = item.output or ""
            if _is_already_masked(output):
                continue
            if not output.strip():
                continue
            item.output = _mask_placeholder(len(output))
            masked += 1

    if masked:
        logger.info(
            "Masked %d old tool output(s); keep_recent_turns=%d est_tokens=%d",
            masked,
            keep_recent_turns,
            estimate_chat_tokens(chat, instructions, tools, cfg),
        )
    return masked


def _evict_until_under_budget(
    chat: Chat,
    instructions: str | None,
    tools: list[Any] | None,
    budget: ContextBudget,
) -> int:
    """Evict oldest turns until under budget or only the latest user turn remains."""
    evicted = 0
    with chat._lock:
        while chat._user_turn_count > 1:
            if estimate_chat_tokens(chat, instructions, tools, budget) <= budget.effective_budget:
                break
            chat._evict_oldest_turn()
            evicted += 1
    return evicted


def preflight_trim(
    chat: Chat,
    instructions: str | None,
    tools: list[Any] | None,
    budget: ContextBudget | None = None,
) -> TrimReport:
    """Proactive trim before LLM request: mask old tool outputs, then evict turns."""
    cfg = budget or ContextBudget.from_env()
    report = TrimReport()
    try:
        report.estimated_before = estimate_chat_tokens(chat, instructions, tools, cfg)
        if report.estimated_before <= cfg.effective_budget:
            report.estimated_after = report.estimated_before
            return report

        report.masked_outputs = mask_old_tool_outputs(
            chat,
            keep_recent_turns=cfg.mask_keep_recent_turns,
            instructions=instructions,
            tools=tools,
            budget=cfg,
        )
        report.estimated_after = estimate_chat_tokens(chat, instructions, tools, cfg)
        if report.estimated_after <= cfg.effective_budget:
            if report.acted:
                logger.warning(
                    "Context preflight: masked %d tool output(s); tokens %d -> %d (budget %d)",
                    report.masked_outputs,
                    report.estimated_before,
                    report.estimated_after,
                    cfg.effective_budget,
                )
            return report

        report.evicted_turns = _evict_until_under_budget(chat, instructions, tools, cfg)
        report.estimated_after = estimate_chat_tokens(chat, instructions, tools, cfg)

        if report.acted:
            logger.warning(
                "Context preflight: masked=%d evicted_turns=%d tokens %d -> %d (budget %d)",
                report.masked_outputs,
                report.evicted_turns,
                report.estimated_before,
                report.estimated_after,
                cfg.effective_budget,
            )
    except Exception:
        logger.exception("Context preflight failed; proceeding without trim")
    return report


def is_context_overflow_error(message: str | None) -> bool:
    if not message:
        return False
    return any(pattern.search(message) for pattern in _OVERFLOW_PATTERNS)


def _hard_reset_keep_recent(chat: Chat, *, keep_turns: int = 2) -> None:
    """Clear buffer but retain the most recent N user turns."""
    with chat._lock:
        user_indices = _user_turn_indices(chat)
        if not user_indices:
            chat.buffer = []
            chat._user_turn_count = 0
            chat._pending_tool_calls = {}
            return

        keep_from = max(0, len(user_indices) - keep_turns)
        start_idx = user_indices[keep_from] if keep_from < len(user_indices) else 0
        kept = list(chat.buffer[start_idx:])
        chat.buffer = kept
        chat._user_turn_count = sum(
            1 for x in chat.buffer if isinstance(x, RealtimeConversationItemUserMessage)
        )
        chat._pending_tool_calls = {
            call_id: fc
            for call_id, fc in chat._pending_tool_calls.items()
            if any(
                isinstance(x, RealtimeConversationItemFunctionCallOutput)
                and x.call_id == call_id
                for x in chat.buffer
            )
        }


def recover_after_overflow(
    chat: Chat,
    instructions: str | None,
    tools: list[Any] | None,
    budget: ContextBudget | None = None,
) -> TrimReport:
    """Aggressive recovery after a context-length error from the LLM backend."""
    cfg = budget or ContextBudget.from_env()
    report = TrimReport()
    try:
        report.estimated_before = estimate_chat_tokens(chat, instructions, tools, cfg)

        report.masked_outputs = mask_old_tool_outputs(
            chat,
            keep_recent_turns=0,
            instructions=instructions,
            tools=tools,
            budget=cfg,
        )
        report.evicted_turns = _evict_until_under_budget(chat, instructions, tools, cfg)
        report.estimated_after = estimate_chat_tokens(chat, instructions, tools, cfg)

        if report.estimated_after > cfg.effective_budget:
            _hard_reset_keep_recent(chat, keep_turns=2)
            report.hard_reset = True
            report.estimated_after = estimate_chat_tokens(chat, instructions, tools, cfg)

        logger.warning(
            "Context overflow recovery: masked=%d evicted_turns=%d hard_reset=%s tokens %d -> %d (budget %d)",
            report.masked_outputs,
            report.evicted_turns,
            report.hard_reset,
            report.estimated_before,
            report.estimated_after,
            cfg.effective_budget,
        )
    except Exception:
        logger.exception("Context overflow recovery failed")
    return report


def build_overflow_apology_text() -> str:
    return "Sorry, that got too long for me to hold in mind — I trimmed our earlier chat."

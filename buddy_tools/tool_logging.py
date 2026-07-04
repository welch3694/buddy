"""Logging helpers for local tool failures (Buddy is voice-only; logs are the operator UI)."""

from __future__ import annotations

import logging
from typing import Any

from buddy_tools.result import ToolExecutionResult

logger = logging.getLogger(__name__)

TOOL_ERROR_PREFIX = "Error:"
_MAX_CONTEXT_VALUE_LEN = 120
_SENSITIVE_KEYS = frozenset({"content", "prompt", "value", "ref_text"})


def is_tool_error_output(output: str) -> bool:
    return output.startswith(TOOL_ERROR_PREFIX)


def is_tool_error(result: ToolExecutionResult) -> bool:
    return is_tool_error_output(result.output)


def safe_tool_context(args: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a log-safe copy of tool arguments (truncated, sensitive keys omitted)."""
    if not args:
        return None

    safe: dict[str, Any] = {}
    for key, value in args.items():
        if key in _SENSITIVE_KEYS:
            if value:
                safe[key] = f"<{len(str(value))} chars>"
            continue
        if isinstance(value, str) and len(value) > _MAX_CONTEXT_VALUE_LEN:
            safe[key] = value[:_MAX_CONTEXT_VALUE_LEN] + "..."
        else:
            safe[key] = value
    return safe or None


def _format_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    return f" context={context!r}"


def log_tool_failure(
    tool_name: str,
    reason: str,
    *,
    exc: BaseException | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Log a tool failure at WARNING (expected) or ERROR with traceback (unexpected)."""
    message = f"Tool {tool_name} failed: {reason}{_format_context(context)}"
    if exc is not None:
        logger.exception(message)
    else:
        logger.warning(message)


def tool_error(
    tool_name: str,
    reason: str,
    *,
    context: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    """Log an expected tool failure and return a standard error result for the LLM."""
    log_tool_failure(tool_name, reason, context=context)
    return ToolExecutionResult(output=f"{TOOL_ERROR_PREFIX} {reason}")

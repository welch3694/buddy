"""Turn receipts and claim-without-tool detection for the local tool executor."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from buddy_tools.core.result import ToolExecutionResult
from buddy_tools.core.tool_logging import is_tool_error, safe_tool_context

ReceiptStatus = Literal["ok", "error", "skipped"]

# Prefer multi-word phrases; bare "done" / "starting" use word boundaries.
_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), label)
    for pattern, label in (
        (r"\bi['’]?m starting\b", "i'm starting"),
        (r"\bi am starting\b", "i am starting"),
        (r"\bi['’]?ve started\b", "i've started"),
        (r"\bi have started\b", "i have started"),
        (r"\bstarting\b", "starting"),
        (r"\bsaved\b", "saved"),
        (r"\bremembered\b", "remembered"),
        (r"\bi['’]?ll remember\b", "i'll remember"),
        (r"\bmake sure to remember\b", "make sure to remember"),
        (r"\bcancelled\b", "cancelled"),
        (r"\bcanceled\b", "canceled"),
        (r"\bupdated\b", "updated"),
        (r"\bi['’]?m done\b", "i'm done"),
        (r"\ball done\b", "all done"),
        (r"\bdone\b", "done"),
    )
)


@dataclass(frozen=True)
class ToolReceipt:
    tool: str
    args_summary: dict[str, Any] | None
    status: ReceiptStatus


def make_tool_receipt(
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    result: ToolExecutionResult | None = None,
    status: ReceiptStatus | None = None,
) -> ToolReceipt:
    """Build a receipt from a tool name, args, and either a result or explicit status."""
    if status is None:
        if result is None:
            raise ValueError("make_tool_receipt requires result or status")
        status = "error" if is_tool_error(result) else "ok"
    return ToolReceipt(
        tool=tool_name,
        args_summary=safe_tool_context(args),
        status=status,
    )


def find_action_claims(text: str) -> list[str]:
    """Return distinct action-claim phrases matched in assistant text (order of first hit)."""
    if not text or not text.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern, label in _CLAIM_PATTERNS:
        if label in seen:
            continue
        if pattern.search(text):
            found.append(label)
            seen.add(label)
    return found


def has_matching_receipt(receipts: Sequence[ToolReceipt], tool_name: str) -> bool:
    """True when any successful (ok) receipt is for the named tool.

    Error/skipped receipts do not satisfy a required tool — the stashed intent
    must still run (or be retried) with correct arguments.
    """
    return any(receipt.tool == tool_name and receipt.status == "ok" for receipt in receipts)


def claims_without_receipt(text: str, receipts: Sequence[ToolReceipt]) -> bool:
    """True when text claims an action and no tool ran (or was skipped) this turn."""
    if receipts:
        return False
    return bool(find_action_claims(text))

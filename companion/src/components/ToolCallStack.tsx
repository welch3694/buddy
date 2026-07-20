import { useEffect } from "react";
import type { ToolCallToast } from "../types/bridge";

/** Minimum visible duration for each tool-call summary (#152). */
export const TOOL_CALL_TTL_MS = 10_000;

type ToolCallStackProps = {
  toolCalls: ToolCallToast[];
  onExpire: (id: string) => void;
};

export function ToolCallStack({ toolCalls, onExpire }: ToolCallStackProps) {
  useEffect(() => {
    if (toolCalls.length === 0) return;

    const now = Date.now();
    const timers: number[] = [];

    for (const entry of toolCalls) {
      const remaining = Math.max(0, entry.receivedAt + TOOL_CALL_TTL_MS - now);
      const timer = window.setTimeout(() => onExpire(entry.id), remaining);
      timers.push(timer);
    }

    return () => {
      for (const timer of timers) window.clearTimeout(timer);
    };
  }, [toolCalls, onExpire]);

  if (toolCalls.length === 0) return null;

  return (
    <div className="tool-call-stack" aria-live="polite" aria-label="Tool calls">
      <span className="tool-call-stack__label">TOOLS</span>
      <ul className="tool-call-stack__list">
        {toolCalls.map((entry) => (
          <li
            key={entry.id}
            className={`tool-call-stack__row tool-call-stack__row--${entry.status}`}
          >
            <span className="tool-call-stack__summary">{entry.summary}</span>
            <span className="tool-call-stack__status">{entry.status.toUpperCase()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

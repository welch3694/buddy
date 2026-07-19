export const TURN_STATES = [
  "listening",
  "holding",
  "generating",
  "speaking",
  "paused",
] as const;

export type TurnState = (typeof TURN_STATES)[number];

export type ConnectionStatus = "connecting" | "connected" | "disconnected";

export type TurnStateEvent = {
  type: "turn_state";
  state: TurnState;
  reason?: string | null;
  turn_id?: string | null;
  turn_revision?: number | null;
  ts: string;
};

export type BridgeEvent = TurnStateEvent | { type: string; [key: string]: unknown };

export function isTurnState(value: unknown): value is TurnState {
  return typeof value === "string" && (TURN_STATES as readonly string[]).includes(value);
}

export function isTurnStateEvent(value: unknown): value is TurnStateEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return event.type === "turn_state" && isTurnState(event.state);
}

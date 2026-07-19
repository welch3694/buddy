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

export type PersonaInfo = {
  id: string;
  name: string;
  memoryNamespace: string;
  voiceId: string | null;
};

export type PersonaEvent = {
  type: "persona";
  id: string;
  name: string;
  memory_namespace: string;
  voice_id?: string | null;
  ts: string;
};

export type BridgeEvent = TurnStateEvent | PersonaEvent | { type: string; [key: string]: unknown };

export function isTurnState(value: unknown): value is TurnState {
  return typeof value === "string" && (TURN_STATES as readonly string[]).includes(value);
}

export function isTurnStateEvent(value: unknown): value is TurnStateEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return event.type === "turn_state" && isTurnState(event.state);
}

export function isPersonaEvent(value: unknown): value is PersonaEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return (
    event.type === "persona" &&
    typeof event.id === "string" &&
    typeof event.name === "string" &&
    typeof event.memory_namespace === "string"
  );
}

export function personaFromEvent(event: PersonaEvent): PersonaInfo {
  return {
    id: event.id,
    name: event.name,
    memoryNamespace: event.memory_namespace,
    voiceId: typeof event.voice_id === "string" ? event.voice_id : null,
  };
}

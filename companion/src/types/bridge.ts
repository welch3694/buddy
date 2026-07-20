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

export type AssistantTextEvent = {
  type: "assistant_text";
  text: string;
  turn_id?: string | null;
  turn_revision?: number | null;
  ts: string;
};

export type SpeakingProgressEvent = {
  type: "speaking_progress";
  progress: number;
  played_ms: number;
  total_ms: number;
  /** True once TTS has enqueued AUDIO_RESPONSE_DONE (total audio length locked). */
  total_final?: boolean;
  ts: string;
};

/** Live PCM playback sample for caption sync (not the raw bridge ratio). */
export type SpeakingPlayback = {
  playedMs: number;
  totalMs: number;
  /** True when synth finished; denominator should be real audio ms only. */
  totalFinal: boolean;
};

/** Inactive pulse session (or cleared state). */
export type PulseStateInactive = {
  type: "pulse_state";
  active: false;
  ts: string;
};

/** Salient active pulse snapshot from the companion bridge (#118). */
export type PulseStateActive = {
  type: "pulse_state";
  active: true;
  skill_name: string;
  status: string;
  phase: string;
  pulse_mode: string;
  pending_cue: string | null;
  cue_priority?: string | null;
  pulse_in_flight: boolean;
  narrator_muted?: boolean;
  tick_count?: number;
  started_at?: string;
  last_tick_at?: string | null;
  vars: Record<string, unknown>;
  camera_labels?: Record<string, string | null>;
  ts: string;
};

export type PulseStateEvent = PulseStateInactive | PulseStateActive;

export type BridgeEvent =
  | TurnStateEvent
  | PersonaEvent
  | AssistantTextEvent
  | SpeakingProgressEvent
  | PulseStateEvent
  | { type: string; [key: string]: unknown };

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

export function isAssistantTextEvent(value: unknown): value is AssistantTextEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return event.type === "assistant_text" && typeof event.text === "string";
}

export function isSpeakingProgressEvent(value: unknown): value is SpeakingProgressEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return (
    event.type === "speaking_progress" &&
    typeof event.progress === "number" &&
    Number.isFinite(event.progress) &&
    typeof event.played_ms === "number" &&
    typeof event.total_ms === "number"
  );
}

export function isPulseStateEvent(value: unknown): value is PulseStateEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  if (event.type !== "pulse_state" || typeof event.active !== "boolean") return false;
  if (!event.active) return true;
  return (
    typeof event.skill_name === "string" &&
    typeof event.status === "string" &&
    typeof event.phase === "string" &&
    typeof event.pulse_mode === "string" &&
    typeof event.pulse_in_flight === "boolean" &&
    event.vars !== null &&
    typeof event.vars === "object" &&
    !Array.isArray(event.vars) &&
    (event.pending_cue === null || typeof event.pending_cue === "string")
  );
}

/** Resolve active camera id → label from bridge vars + camera_labels. */
export function resolveActiveCamera(event: PulseStateActive): string | null {
  const raw = event.vars.current_camera;
  if (typeof raw !== "string" || !raw) return null;
  const label = event.camera_labels?.[raw];
  if (typeof label === "string" && label.trim()) return label;
  return raw;
}

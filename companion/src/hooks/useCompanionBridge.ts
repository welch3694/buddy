import { useCallback, useEffect, useRef, useState } from "react";
import {
  isAssistantTextEvent,
  isPersonaEvent,
  isPulseStateEvent,
  isSpeakingProgressEvent,
  isThemeEvent,
  isToolCallEvent,
  isTurnState,
  isTurnStateEvent,
  personaFromEvent,
  themeFromEvent,
  TURN_STATES,
  type ConnectionStatus,
  type PersonaInfo,
  type PulseStateActive,
  type PulseStateEvent,
  type SpeakingPlayback,
  type ThemeInfo,
  type ToolCallEvent,
  type ToolCallToast,
  type TurnState,
} from "../types/bridge";
import { applyThemeTokens } from "../theme/applyThemeTokens";

const DEFAULT_WS_URL = "ws://127.0.0.1:8766";
const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 8000;
const MOCK_INTERVAL_MS = 2800;
const MOCK_PROGRESS_TICK_MS = 80;
const MOCK_SPEAKING_DURATION_MS = 2200;
const TOOL_CALL_STACK_CAP = 12;

const MOCK_PERSONA: PersonaInfo = {
  id: "mock",
  name: "Mock",
  memoryNamespace: "mock",
  voiceId: null,
};

const MOCK_SPEAKING_CAPTION =
  "Routing signal through the local lattice — presence confirmed.";

const MOCK_GENERATING_CAPTION = "Routing signal through the local lattice";

const MOCK_PULSE_INACTIVE: PulseStateEvent = {
  type: "pulse_state",
  active: false,
  ts: new Date(0).toISOString(),
};

const MOCK_PULSE_ACTIVE: PulseStateActive = {
  type: "pulse_state",
  active: true,
  skill_name: "live-director",
  status: "active",
  phase: "running",
  pulse_mode: "directed",
  pending_cue: "advance_camera",
  cue_priority: "conversational",
  pulse_in_flight: false,
  vars: { current_camera: "cam1", beat: 2 },
  camera_labels: { cam1: "Wide", cam2: "Close" },
  senses: [
    { key: "phase", label: "PHASE", value: "running" },
    { key: "pulse_mode", label: "MODE", value: "directed" },
    { key: "current_camera", label: "CAMERA", value: "Wide" },
    { key: "pending_cue", label: "CUE", value: "advance_camera" },
  ],
  ts: new Date(0).toISOString(),
};

const MOCK_TOOL_EVENTS: Omit<ToolCallEvent, "ts">[] = [
  {
    type: "tool_call",
    tool: "list_skills",
    status: "ok",
    summary: "list_skills · ok",
    source: "llm",
  },
  {
    type: "tool_call",
    tool: "read_memory",
    status: "ok",
    summary: "read_memory · ok · scope=user",
    source: "silent",
  },
];

function resolveWsUrl(): string {
  return import.meta.env.VITE_COMPANION_WS_URL?.trim() || DEFAULT_WS_URL;
}

function useMockMode(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  if (params.get("mock") === "1") return true;
  // OBS setup URL: ?debug=1 enables mock speaking cycles (never on production /obs URL).
  if (
    params.get("debug") === "1" &&
    window.location.pathname.startsWith("/obs")
  ) {
    return true;
  }
  return false;
}

function appendCaptionChunk(existing: string, chunk: string): string {
  if (!chunk) return existing;
  if (!existing) return chunk;
  // Chunks are usually contiguous stream pieces; avoid double spaces.
  if (existing.endsWith(" ") || chunk.startsWith(" ")) {
    return existing + chunk;
  }
  // If chunk looks like a fresh sentence start after whitespace-stripped prior, join with space
  if (/^[A-Z0-9"']/.test(chunk) && /[.?!:]$/.test(existing.trimEnd())) {
    return `${existing.trimEnd()} ${chunk}`;
  }
  return existing + chunk;
}

function toastFromToolCallEvent(event: ToolCallEvent): ToolCallToast {
  const source = event.source === "silent" ? "silent" : "llm";
  return {
    id: `${event.ts}-${event.tool}-${Math.random().toString(36).slice(2, 8)}`,
    tool: event.tool,
    status: event.status,
    summary: event.summary,
    source,
    receivedAt: Date.now(),
  };
}

function prependToolCall(
  prev: ToolCallToast[],
  event: ToolCallEvent,
): ToolCallToast[] {
  return [toastFromToolCallEvent(event), ...prev].slice(0, TOOL_CALL_STACK_CAP);
}

export type CompanionBridgeState = {
  connection: ConnectionStatus;
  turnState: TurnState | null;
  reason: string | null;
  persona: PersonaInfo | null;
  theme: ThemeInfo | null;
  captionText: string;
  /** PCM playback timing from the bridge; null until first sample / mock tick. */
  speakingPlayback: SpeakingPlayback | null;
  /** Latest pulse_state from the bridge; null until first event. */
  pulseState: PulseStateEvent | null;
  /** Recent tool-call summaries for the HUD stack (#152). */
  toolCalls: ToolCallToast[];
  expireToolCall: (id: string) => void;
  mock: boolean;
};

export function useCompanionBridge(): CompanionBridgeState {
  const mock = useMockMode();
  const [connection, setConnection] = useState<ConnectionStatus>(
    mock ? "connected" : "connecting",
  );
  const [turnState, setTurnState] = useState<TurnState | null>(
    mock ? "listening" : null,
  );
  const [reason, setReason] = useState<string | null>(mock ? "mock" : null);
  const [persona, setPersona] = useState<PersonaInfo | null>(mock ? MOCK_PERSONA : null);
  const [theme, setTheme] = useState<ThemeInfo | null>(null);
  const [captionText, setCaptionText] = useState("");
  const [speakingPlayback, setSpeakingPlayback] = useState<SpeakingPlayback | null>(null);
  const [pulseState, setPulseState] = useState<PulseStateEvent | null>(
    mock ? MOCK_PULSE_INACTIVE : null,
  );
  const [toolCalls, setToolCalls] = useState<ToolCallToast[]>([]);
  const backoffRef = useRef(MIN_BACKOFF_MS);
  const wsRef = useRef<WebSocket | null>(null);
  const closedRef = useRef(false);
  const captionTurnRef = useRef<{ turnId: string | null; revision: number | null }>({
    turnId: null,
    revision: null,
  });
  const mockToolIndexRef = useRef(0);

  const expireToolCall = useCallback((id: string) => {
    setToolCalls((prev) => prev.filter((entry) => entry.id !== id));
  }, []);

  useEffect(() => {
    if (!mock) return;

    let index = 0;
    let progressTimer: number | undefined;
    setConnection("connected");
    setTurnState(TURN_STATES[0]);
    setReason("mock");
    setPersona(MOCK_PERSONA);
    setCaptionText("");
    setSpeakingPlayback(null);
    setPulseState(MOCK_PULSE_INACTIVE);
    setToolCalls([]);
    mockToolIndexRef.current = 0;

    const clearProgressTimer = () => {
      if (progressTimer !== undefined) {
        window.clearInterval(progressTimer);
        progressTimer = undefined;
      }
    };

    const id = window.setInterval(() => {
      index = (index + 1) % TURN_STATES.length;
      const next = TURN_STATES[index];
      setTurnState(next);
      setReason("mock");
      clearProgressTimer();

      // Alternate idle / active pulse so SENSES HUD can be exercised without voice.
      const pulseActive = index % 2 === 1;
      const now = new Date().toISOString();
      if (pulseActive) {
        const phase = next === "paused" ? "paused" : "running";
        const cue = next === "holding" ? "advance_camera" : null;
        const camId = next === "speaking" ? "cam2" : "cam1";
        const camLabel =
          MOCK_PULSE_ACTIVE.camera_labels?.[camId] ?? camId;
        const active: PulseStateEvent = {
          type: "pulse_state",
          active: true,
          skill_name: MOCK_PULSE_ACTIVE.skill_name,
          status: MOCK_PULSE_ACTIVE.status,
          phase,
          pulse_mode: MOCK_PULSE_ACTIVE.pulse_mode,
          pending_cue: cue,
          cue_priority: MOCK_PULSE_ACTIVE.cue_priority,
          pulse_in_flight: next === "generating" || next === "speaking",
          vars: {
            current_camera: camId,
            beat: index,
          },
          camera_labels: MOCK_PULSE_ACTIVE.camera_labels,
          senses: [
            { key: "phase", label: "PHASE", value: phase },
            { key: "pulse_mode", label: "MODE", value: MOCK_PULSE_ACTIVE.pulse_mode },
            { key: "current_camera", label: "CAMERA", value: camLabel },
            { key: "pending_cue", label: "CUE", value: cue ?? "—" },
          ],
          ts: now,
        };
        setPulseState(active);
      } else {
        setPulseState({ type: "pulse_state", active: false, ts: now });
      }

      // Inject tool-call toasts while generating / speaking so the stack is visible in mock.
      if (next === "generating" || next === "speaking") {
        const mockEvent = MOCK_TOOL_EVENTS[mockToolIndexRef.current % MOCK_TOOL_EVENTS.length];
        mockToolIndexRef.current += 1;
        setToolCalls((prev) =>
          prependToolCall(prev, { ...mockEvent, ts: now }),
        );
      }

      if (next === "generating") {
        setCaptionText(MOCK_GENERATING_CAPTION);
        setSpeakingPlayback(null);
      } else if (next === "speaking") {
        setCaptionText(MOCK_SPEAKING_CAPTION);
        // Simulate TTS filling the queue gradually (frontier ahead of playhead).
        const started = performance.now();
        progressTimer = window.setInterval(() => {
          const elapsed = performance.now() - started;
          const playedMs = Math.min(MOCK_SPEAKING_DURATION_MS, elapsed);
          // Enqueued grows ahead of played, then locks when synth "finishes".
          const synthDone = elapsed >= MOCK_SPEAKING_DURATION_MS * 0.75;
          const totalMs = synthDone
            ? MOCK_SPEAKING_DURATION_MS
            : Math.min(
                MOCK_SPEAKING_DURATION_MS,
                Math.max(playedMs + 400, elapsed * 1.25),
              );
          setSpeakingPlayback({
            playedMs,
            totalMs,
            totalFinal: synthDone,
          });
        }, MOCK_PROGRESS_TICK_MS);
      } else if (next === "paused") {
        setCaptionText(MOCK_SPEAKING_CAPTION);
      } else {
        setSpeakingPlayback(null);
      }
    }, MOCK_INTERVAL_MS);

    return () => {
      window.clearInterval(id);
      clearProgressTimer();
    };
  }, [mock]);

  useEffect(() => {
    if (mock) return;

    closedRef.current = false;
    let reconnectTimer: number | undefined;

    const connect = () => {
      if (closedRef.current) return;

      setConnection((prev) => (prev === "connected" ? prev : "connecting"));

      let ws: WebSocket;
      try {
        ws = new WebSocket(resolveWsUrl());
      } catch {
        setConnection("disconnected");
        scheduleReconnect();
        return;
      }

      wsRef.current = ws;

      ws.onopen = () => {
        backoffRef.current = MIN_BACKOFF_MS;
        setConnection("connected");
      };

      ws.onmessage = (event) => {
        let payload: unknown;
        try {
          payload = JSON.parse(String(event.data));
        } catch {
          return;
        }

        if (isPersonaEvent(payload)) {
          setPersona(personaFromEvent(payload));
          return;
        }

        if (isThemeEvent(payload)) {
          const next = themeFromEvent(payload);
          applyThemeTokens(next.tokens);
          setTheme(next);
          return;
        }

        if (isAssistantTextEvent(payload)) {
          const turnId = typeof payload.turn_id === "string" ? payload.turn_id : null;
          const revision =
            typeof payload.turn_revision === "number" ? payload.turn_revision : null;
          const sameTurn =
            captionTurnRef.current.turnId === turnId &&
            captionTurnRef.current.revision === revision;

          captionTurnRef.current = { turnId, revision };
          setCaptionText((prev) =>
            sameTurn ? appendCaptionChunk(prev, payload.text) : payload.text,
          );
          return;
        }

        if (isSpeakingProgressEvent(payload)) {
          setSpeakingPlayback({
            playedMs: Math.max(0, payload.played_ms),
            totalMs: Math.max(0, payload.total_ms),
            totalFinal: payload.total_final === true,
          });
          return;
        }

        if (isPulseStateEvent(payload)) {
          setPulseState(payload);
          return;
        }

        if (isToolCallEvent(payload)) {
          setToolCalls((prev) => prependToolCall(prev, payload));
          return;
        }

        if (!isTurnStateEvent(payload)) return;
        if (!isTurnState(payload.state)) return;

        setTurnState(payload.state);
        setReason(typeof payload.reason === "string" ? payload.reason : null);

        // Fresh generate cycle — clear prior caption buffer for the new turn
        if (payload.state === "generating") {
          captionTurnRef.current = {
            turnId: typeof payload.turn_id === "string" ? payload.turn_id : null,
            revision:
              typeof payload.turn_revision === "number" ? payload.turn_revision : null,
          };
          setCaptionText("");
          setSpeakingPlayback(null);
        } else if (payload.state === "listening" || payload.state === "holding") {
          setSpeakingPlayback(null);
        }
      };

      ws.onerror = () => {
        // onclose handles reconnect; keep UI from crashing
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (closedRef.current) return;
        setConnection("disconnected");
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (closedRef.current) return;
      const delay = backoffRef.current;
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS);
      reconnectTimer = window.setTimeout(connect, delay);
    };

    connect();

    return () => {
      closedRef.current = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        ws.close();
      }
    };
  }, [mock]);

  return {
    connection,
    turnState,
    reason,
    persona,
    theme,
    captionText,
    speakingPlayback,
    pulseState,
    toolCalls,
    expireToolCall,
    mock,
  };
}

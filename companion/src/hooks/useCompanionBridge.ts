import { useEffect, useRef, useState } from "react";
import {
  isAssistantTextEvent,
  isPersonaEvent,
  isPulseStateEvent,
  isSpeakingProgressEvent,
  isTurnState,
  isTurnStateEvent,
  personaFromEvent,
  TURN_STATES,
  type ConnectionStatus,
  type PersonaInfo,
  type PulseStateActive,
  type PulseStateEvent,
  type SpeakingPlayback,
  type TurnState,
} from "../types/bridge";

const DEFAULT_WS_URL = "ws://127.0.0.1:8766";
const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 8000;
const MOCK_INTERVAL_MS = 2800;
const MOCK_PROGRESS_TICK_MS = 80;
const MOCK_SPEAKING_DURATION_MS = 2200;

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
  ts: new Date(0).toISOString(),
};

function resolveWsUrl(): string {
  return import.meta.env.VITE_COMPANION_WS_URL?.trim() || DEFAULT_WS_URL;
}

function useMockMode(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("mock") === "1";
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

export type CompanionBridgeState = {
  connection: ConnectionStatus;
  turnState: TurnState | null;
  reason: string | null;
  persona: PersonaInfo | null;
  captionText: string;
  /** PCM playback timing from the bridge; null until first sample / mock tick. */
  speakingPlayback: SpeakingPlayback | null;
  /** Latest pulse_state from the bridge; null until first event. */
  pulseState: PulseStateEvent | null;
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
  const [captionText, setCaptionText] = useState("");
  const [speakingPlayback, setSpeakingPlayback] = useState<SpeakingPlayback | null>(null);
  const [pulseState, setPulseState] = useState<PulseStateEvent | null>(
    mock ? MOCK_PULSE_INACTIVE : null,
  );
  const backoffRef = useRef(MIN_BACKOFF_MS);
  const wsRef = useRef<WebSocket | null>(null);
  const closedRef = useRef(false);
  const captionTurnRef = useRef<{ turnId: string | null; revision: number | null }>({
    turnId: null,
    revision: null,
  });

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
        const active: PulseStateEvent = {
          type: "pulse_state",
          active: true,
          skill_name: MOCK_PULSE_ACTIVE.skill_name,
          status: MOCK_PULSE_ACTIVE.status,
          phase: next === "paused" ? "paused" : "running",
          pulse_mode: MOCK_PULSE_ACTIVE.pulse_mode,
          pending_cue: next === "holding" ? "advance_camera" : null,
          cue_priority: MOCK_PULSE_ACTIVE.cue_priority,
          pulse_in_flight: next === "generating" || next === "speaking",
          vars: {
            current_camera: next === "speaking" ? "cam2" : "cam1",
            beat: index,
          },
          camera_labels: MOCK_PULSE_ACTIVE.camera_labels,
          ts: now,
        };
        setPulseState(active);
      } else {
        setPulseState({ type: "pulse_state", active: false, ts: now });
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
    captionText,
    speakingPlayback,
    pulseState,
    mock,
  };
}

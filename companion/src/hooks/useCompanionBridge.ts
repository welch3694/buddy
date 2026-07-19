import { useEffect, useRef, useState } from "react";
import {
  isTurnState,
  isTurnStateEvent,
  TURN_STATES,
  type ConnectionStatus,
  type TurnState,
} from "../types/bridge";

const DEFAULT_WS_URL = "ws://127.0.0.1:8766";
const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 8000;
const MOCK_INTERVAL_MS = 2200;

function resolveWsUrl(): string {
  return import.meta.env.VITE_COMPANION_WS_URL?.trim() || DEFAULT_WS_URL;
}

function useMockMode(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("mock") === "1";
}

export type CompanionBridgeState = {
  connection: ConnectionStatus;
  turnState: TurnState | null;
  reason: string | null;
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
  const backoffRef = useRef(MIN_BACKOFF_MS);
  const wsRef = useRef<WebSocket | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    if (!mock) return;

    let index = 0;
    setConnection("connected");
    setTurnState(TURN_STATES[0]);
    setReason("mock");

    const id = window.setInterval(() => {
      index = (index + 1) % TURN_STATES.length;
      setTurnState(TURN_STATES[index]);
      setReason("mock");
    }, MOCK_INTERVAL_MS);

    return () => window.clearInterval(id);
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

        if (!isTurnStateEvent(payload)) return;
        if (!isTurnState(payload.state)) return;

        setTurnState(payload.state);
        setReason(typeof payload.reason === "string" ? payload.reason : null);
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

  return { connection, turnState, reason, mock };
}

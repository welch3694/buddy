import type { ConnectionStatus } from "../types/bridge";

type BridgeStatusProps = {
  connection: ConnectionStatus;
  mock: boolean;
};

const LABELS: Record<ConnectionStatus, string> = {
  connected: "LINK ACTIVE",
  connecting: "LINKING…",
  disconnected: "LINK DOWN",
};

export function BridgeStatus({ connection, mock }: BridgeStatusProps) {
  return (
    <div className={`bridge-status bridge-status--${connection}`} aria-live="polite">
      <span className="bridge-status__dot" aria-hidden="true" />
      <span className="bridge-status__label">
        {mock ? "MOCK MODE" : LABELS[connection]}
      </span>
    </div>
  );
}

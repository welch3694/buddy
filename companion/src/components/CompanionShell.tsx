import { BridgeStatus } from "./BridgeStatus";
import { CentralOrb } from "./CentralOrb";
import type { ConnectionStatus, TurnState } from "../types/bridge";

type CompanionShellProps = {
  connection: ConnectionStatus;
  turnState: TurnState | null;
  reason: string | null;
  mock: boolean;
};

export function CompanionShell({
  connection,
  turnState,
  reason,
  mock,
}: CompanionShellProps) {
  const stateLabel =
    connection !== "connected"
      ? "OFFLINE"
      : (turnState ?? "STANDBY").toUpperCase();

  return (
    <div className="shell">
      <div className="shell__atmosphere" aria-hidden="true" />
      <div className="shell__grid" aria-hidden="true" />
      <div className="shell__scanlines" aria-hidden="true" />

      <header className="shell__header">
        <h1 className="shell__brand">BUDDY</h1>
        <BridgeStatus connection={connection} mock={mock} />
      </header>

      <main className="shell__stage">
        <CentralOrb turnState={turnState} connection={connection} />
      </main>

      <footer className="shell__telemetry">
        <div className="shell__tele-block">
          <span className="shell__tele-key">STATE</span>
          <span className="shell__tele-val">{stateLabel}</span>
        </div>
        <div className="shell__tele-block">
          <span className="shell__tele-key">REASON</span>
          <span className="shell__tele-val">
            {connection === "connected" && reason ? reason : "—"}
          </span>
        </div>
        <div className="shell__tele-block shell__tele-block--reserve">
          <span className="shell__tele-key">CAPTIONS</span>
          <span className="shell__tele-val shell__tele-val--dim">STANDBY</span>
        </div>
        <div className="shell__tele-block shell__tele-block--reserve">
          <span className="shell__tele-key">SENSES</span>
          <span className="shell__tele-val shell__tele-val--dim">STANDBY</span>
        </div>
      </footer>
    </div>
  );
}

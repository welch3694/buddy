import { BridgeStatus } from "./BridgeStatus";
import { CentralOrb } from "./CentralOrb";
import { LiveCaptions } from "./LiveCaptions";
import { useCaptionHighlight } from "../hooks/useCaptionHighlight";
import type { ConnectionStatus, PersonaInfo, TurnState } from "../types/bridge";

type CompanionShellProps = {
  connection: ConnectionStatus;
  turnState: TurnState | null;
  reason: string | null;
  persona: PersonaInfo | null;
  captionText: string;
  mock: boolean;
};

export function CompanionShell({
  connection,
  turnState,
  reason,
  persona,
  captionText,
  mock,
}: CompanionShellProps) {
  const captions = useCaptionHighlight(captionText, turnState);

  const stateLabel =
    connection !== "connected"
      ? "OFFLINE"
      : (turnState ?? "STANDBY").toUpperCase();

  const personaName =
    connection === "connected" && persona?.name ? persona.name : "—";

  const captionStatus =
    connection !== "connected"
      ? "—"
      : captions.phase === "idle"
        ? "STANDBY"
        : captions.phase === "speaking"
          ? "LIVE"
          : captions.phase === "settled"
            ? "SETTLED"
            : "BUFFER";

  return (
    <div className="shell">
      <div className="shell__atmosphere" aria-hidden="true" />
      <div className="shell__grid" aria-hidden="true" />
      <div className="shell__scanlines" aria-hidden="true" />

      <header className="shell__header">
        <div className="shell__identity">
          <h1 className="shell__brand">BUDDY</h1>
          <div className="shell__persona" aria-live="polite">
            <span className="shell__persona-key">ACTIVE PERSONA</span>
            <span className="shell__persona-val">{personaName}</span>
          </div>
        </div>
        <BridgeStatus connection={connection} mock={mock} />
      </header>

      <main className="shell__stage">
        <CentralOrb turnState={turnState} connection={connection} />
        <LiveCaptions
          words={captions.words}
          activeWordIndex={captions.activeWordIndex}
          phase={captions.phase}
        />
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
        <div className="shell__tele-block">
          <span className="shell__tele-key">CAPTIONS</span>
          <span
            className={
              captions.phase === "idle" || connection !== "connected"
                ? "shell__tele-val shell__tele-val--dim"
                : "shell__tele-val"
            }
          >
            {captionStatus}
          </span>
        </div>
        <div className="shell__tele-block shell__tele-block--reserve">
          <span className="shell__tele-key">SENSES</span>
          <span className="shell__tele-val shell__tele-val--dim">STANDBY</span>
        </div>
      </footer>
    </div>
  );
}

import { fallbackSenseRows, type PulseStateEvent, type SenseRow } from "../types/bridge";

type PulseSensesHudProps = {
  pulseState: PulseStateEvent | null;
  connected: boolean;
};

function senseRows(pulseState: Extract<PulseStateEvent, { active: true }>): SenseRow[] {
  if (pulseState.senses && pulseState.senses.length > 0) {
    return pulseState.senses;
  }
  return fallbackSenseRows(pulseState);
}

export function PulseSensesHud({ pulseState, connected }: PulseSensesHudProps) {
  const idle =
    !connected || pulseState === null || pulseState.active !== true;

  if (idle) {
    return (
      <div className="shell__tele-block senses-hud senses-hud--idle">
        <span className="shell__tele-key">SENSES</span>
        <span className="shell__tele-val shell__tele-val--dim">STANDBY</span>
      </div>
    );
  }

  const rows = senseRows(pulseState);

  return (
    <div
      className="shell__tele-block senses-hud senses-hud--live"
      aria-live="polite"
      aria-label="Pulse senses"
    >
      <span className="shell__tele-key">SENSES</span>
      <div className="senses-hud__grid">
        {rows.map((row) => (
          <span className="senses-hud__row" key={row.key}>
            <span className="senses-hud__k">{row.label}</span>
            <span
              className={
                row.key === "pending_cue"
                  ? "senses-hud__v senses-hud__v--cue"
                  : "senses-hud__v"
              }
            >
              {row.value}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

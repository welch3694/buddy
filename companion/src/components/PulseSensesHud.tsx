import { resolveActiveCamera, type PulseStateEvent } from "../types/bridge";

type PulseSensesHudProps = {
  pulseState: PulseStateEvent | null;
  connected: boolean;
};

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

  const camera = resolveActiveCamera(pulseState);
  const cue = pulseState.pending_cue?.trim() || "—";

  return (
    <div
      className="shell__tele-block senses-hud senses-hud--live"
      aria-live="polite"
      aria-label="Pulse senses"
    >
      <span className="shell__tele-key">SENSES</span>
      <div className="senses-hud__grid">
        <span className="senses-hud__row">
          <span className="senses-hud__k">PHASE</span>
          <span className="senses-hud__v">{pulseState.phase}</span>
        </span>
        <span className="senses-hud__row">
          <span className="senses-hud__k">MODE</span>
          <span className="senses-hud__v">{pulseState.pulse_mode}</span>
        </span>
        <span className="senses-hud__row">
          <span className="senses-hud__k">CAMERA</span>
          <span className="senses-hud__v">{camera ?? "—"}</span>
        </span>
        <span className="senses-hud__row">
          <span className="senses-hud__k">CUE</span>
          <span className="senses-hud__v senses-hud__v--cue">{cue}</span>
        </span>
      </div>
    </div>
  );
}

import { resolveActiveCamera, type PulseStateEvent } from "../types/bridge";

type PulseSensesHudProps = {
  pulseState: PulseStateEvent | null;
  connected: boolean;
};

function formatVarValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatVars(vars: Record<string, unknown>): string {
  const entries = Object.entries(vars);
  if (entries.length === 0) return "—";
  return entries
    .map(([key, value]) => `${key}=${formatVarValue(value)}`)
    .join(" · ");
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

  const camera = resolveActiveCamera(pulseState);
  const cue = pulseState.pending_cue?.trim() || "—";
  const flight = pulseState.pulse_in_flight ? "IN-FLIGHT" : "IDLE";

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
          <span className="senses-hud__k">STATUS</span>
          <span className="senses-hud__v">{pulseState.status}</span>
        </span>
        <span className="senses-hud__row">
          <span className="senses-hud__k">CAMERA</span>
          <span className="senses-hud__v">{camera ?? "—"}</span>
        </span>
        <span className="senses-hud__row">
          <span className="senses-hud__k">CUE</span>
          <span className="senses-hud__v senses-hud__v--cue">{cue}</span>
        </span>
        <span className="senses-hud__row">
          <span className="senses-hud__k">FLIGHT</span>
          <span
            className={
              pulseState.pulse_in_flight
                ? "senses-hud__v senses-hud__v--flight"
                : "senses-hud__v"
            }
          >
            {flight}
          </span>
        </span>
        <span className="senses-hud__row senses-hud__row--vars">
          <span className="senses-hud__k">VARS</span>
          <span className="senses-hud__v senses-hud__v--vars">
            {formatVars(pulseState.vars)}
          </span>
        </span>
      </div>
    </div>
  );
}

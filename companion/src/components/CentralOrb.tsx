import type { ConnectionStatus, TurnState } from "../types/bridge";

type CentralOrbProps = {
  turnState: TurnState | null;
  connection: ConnectionStatus;
};

function visualState(
  turnState: TurnState | null,
  connection: ConnectionStatus,
): TurnState | "offline" {
  if (connection !== "connected") return "offline";
  return turnState ?? "offline";
}

export function CentralOrb({ turnState, connection }: CentralOrbProps) {
  const mode = visualState(turnState, connection);

  return (
    <div className={`orb orb--${mode}`} role="img" aria-label={`Presence: ${mode}`}>
      <div className="orb__bloom" aria-hidden="true" />
      <div className="orb__ring orb__ring--outer" aria-hidden="true" />
      <div className="orb__ring orb__ring--inner" aria-hidden="true" />
      <div className="orb__core" aria-hidden="true" />
      <div className="orb__shimmer" aria-hidden="true" />
    </div>
  );
}

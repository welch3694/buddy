import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BridgeStatus } from "../components/BridgeStatus";
import { CentralOrb } from "../components/CentralOrb";
import { useCompanionBridge } from "../hooks/useCompanionBridge";
import { OBS_FADE_OUT_MS } from "../obsTiming";

/**
 * Transparent OBS Browser Source widget: speaking orb (+ optional persona name).
 * Production: http://127.0.0.1:5173/obs/speaking
 * With name:  http://127.0.0.1:5173/obs/speaking?name=1
 * Setup:     http://127.0.0.1:5173/obs/speaking?debug=1
 */
export function ObsSpeakingPage() {
  const [searchParams] = useSearchParams();
  const debug = searchParams.get("debug") === "1";
  const showName = searchParams.get("name") === "1";
  const bridge = useCompanionBridge();

  const speaking =
    bridge.connection === "connected" && bridge.turnState === "speaking";

  const [showOrb, setShowOrb] = useState(false);
  const [fading, setFading] = useState(false);
  /** Keep last persona label through the fade-out so the name does not vanish early. */
  const frozenNameRef = useRef<string | null>(null);

  useEffect(() => {
    document.documentElement.classList.add("obs-transparent");
    return () => {
      document.documentElement.classList.remove("obs-transparent");
    };
  }, []);

  useEffect(() => {
    const liveName = bridge.persona?.name?.trim();
    if (speaking && liveName) {
      frozenNameRef.current = liveName;
    }
  }, [speaking, bridge.persona?.name]);

  useEffect(() => {
    if (speaking) {
      setShowOrb(true);
      setFading(false);
      return;
    }
    if (!showOrb) return;

    setFading(true);
    const id = window.setTimeout(() => {
      setShowOrb(false);
      setFading(false);
    }, OBS_FADE_OUT_MS);
    return () => window.clearTimeout(id);
  }, [speaking, showOrb]);

  const personaName = showName
    ? bridge.persona?.name?.trim() || frozenNameRef.current
    : null;

  return (
    <div className="obs-speaking">
      {debug ? (
        <div className="obs-speaking__debug">
          <BridgeStatus connection={bridge.connection} mock={bridge.mock} />
          <span className="obs-speaking__debug-state" aria-live="polite">
            {bridge.turnState ?? "—"}
            {showName && personaName ? ` · ${personaName}` : ""}
          </span>
        </div>
      ) : null}
      {showOrb ? (
        <div
          className={
            fading
              ? "obs-speaking__orb obs-speaking__orb--fade-out"
              : "obs-speaking__orb"
          }
        >
          <div className="obs-speaking__stack">
            {/* Keep speaking visuals during fade even after turnState leaves speaking. */}
            <CentralOrb turnState="speaking" connection="connected" />
            {personaName ? (
              <span className="obs-speaking__persona">{personaName}</span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

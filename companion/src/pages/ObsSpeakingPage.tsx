import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BridgeStatus } from "../components/BridgeStatus";
import { CentralOrb } from "../components/CentralOrb";
import { useCompanionBridge } from "../hooks/useCompanionBridge";

/** Match orb breath timing — long enough to read as a soft exit, not a hard cut. */
const FADE_OUT_MS = 450;

/**
 * Transparent OBS Browser Source widget: speaking orb only.
 * Production: http://127.0.0.1:5173/obs/speaking
 * Setup:     http://127.0.0.1:5173/obs/speaking?debug=1
 */
export function ObsSpeakingPage() {
  const [searchParams] = useSearchParams();
  const debug = searchParams.get("debug") === "1";
  const bridge = useCompanionBridge();

  const speaking =
    bridge.connection === "connected" && bridge.turnState === "speaking";

  const [showOrb, setShowOrb] = useState(false);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    document.documentElement.classList.add("obs-transparent");
    return () => {
      document.documentElement.classList.remove("obs-transparent");
    };
  }, []);

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
    }, FADE_OUT_MS);
    return () => window.clearTimeout(id);
  }, [speaking, showOrb]);

  return (
    <div className="obs-speaking">
      {debug ? (
        <div className="obs-speaking__debug">
          <BridgeStatus connection={bridge.connection} mock={bridge.mock} />
          <span className="obs-speaking__debug-state" aria-live="polite">
            {bridge.turnState ?? "—"}
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
          {/* Keep speaking visuals during fade even after turnState leaves speaking. */}
          <CentralOrb turnState="speaking" connection="connected" />
        </div>
      ) : null}
    </div>
  );
}

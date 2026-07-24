import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { BridgeStatus } from "../components/BridgeStatus";
import { ObsKaraokeCaptions } from "../components/ObsKaraokeCaptions";
import { useCaptionHighlight } from "../hooks/useCaptionHighlight";
import { useCompanionBridge } from "../hooks/useCompanionBridge";

/**
 * Transparent OBS Browser Source widget: single-line karaoke captions.
 * Production: http://127.0.0.1:5173/obs/captions
 * Setup:     http://127.0.0.1:5173/obs/captions?debug=1
 */
export function ObsCaptionsPage() {
  const [searchParams] = useSearchParams();
  const debug = searchParams.get("debug") === "1";
  const bridge = useCompanionBridge();
  const captions = useCaptionHighlight(
    bridge.captionText,
    bridge.turnState,
    bridge.speakingPlayback,
  );

  useEffect(() => {
    document.documentElement.classList.add("obs-transparent");
    return () => {
      document.documentElement.classList.remove("obs-transparent");
    };
  }, []);

  const live = bridge.connection === "connected" || bridge.mock;

  return (
    <div className="obs-captions">
      {debug ? (
        <div className="obs-captions__debug">
          <BridgeStatus connection={bridge.connection} mock={bridge.mock} />
          <span className="obs-captions__debug-state" aria-live="polite">
            {bridge.turnState ?? "—"} · {captions.phase}
          </span>
        </div>
      ) : null}
      {live ? (
        <ObsKaraokeCaptions
          words={captions.words}
          activeWordIndex={captions.activeWordIndex}
          phase={captions.phase}
          captionText={captions.displayText || bridge.captionText}
          speakingPlayback={bridge.speakingPlayback}
        />
      ) : null}
    </div>
  );
}

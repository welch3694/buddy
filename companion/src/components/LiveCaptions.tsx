import type { CaptionPhase } from "../hooks/useCaptionHighlight";

type LiveCaptionsProps = {
  words: string[];
  activeWordIndex: number;
  phase: CaptionPhase;
  /** When true, render nothing instead of the idle placeholder (OBS stream-safe). */
  hideWhenEmpty?: boolean;
};

export function LiveCaptions({
  words,
  activeWordIndex,
  phase,
  hideWhenEmpty = false,
}: LiveCaptionsProps) {
  if (phase === "idle" || words.length === 0) {
    if (hideWhenEmpty) return null;
    return (
      <div className="captions captions--empty" aria-live="polite">
        <span className="captions__placeholder">AWAITING TRANSMISSION</span>
      </div>
    );
  }

  return (
    <div
      className={`captions captions--${phase}`}
      aria-live="polite"
      aria-atomic="false"
    >
      <p className="captions__line">
        {words.map((word, index) => {
          const spoken = index < activeWordIndex;
          const active = index === activeWordIndex && phase === "speaking";
          const className = [
            "captions__word",
            spoken ? "captions__word--spoken" : "",
            active ? "captions__word--active" : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <span key={`${index}-${word}`} className={className}>
              {word}
              {index < words.length - 1 ? " " : ""}
            </span>
          );
        })}
      </p>
    </div>
  );
}

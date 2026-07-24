import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CaptionPhase } from "../hooks/useCaptionHighlight";
import type { SpeakingPlayback } from "../types/bridge";
import { OBS_FADE_OUT_MS } from "../obsTiming";
import { captionProgressFromPlayback } from "../utils/captionTiming";

/** Slight left of center so upcoming words read more easily. */
const FOCUS_RATIO = 0.42;

type ObsKaraokeCaptionsProps = {
  words: string[];
  activeWordIndex: number;
  phase: CaptionPhase;
  captionText: string;
  speakingPlayback: SpeakingPlayback | null;
};

/**
 * Char-weighted fractional index (0 .. words.length-1) for smooth scroll
 * between word centers — same weighting as highlight progress.
 */
function fractionalWordIndex(words: string[], progress: number): number {
  if (words.length === 0) return 0;
  if (words.length === 1) return 0;
  const clamped = Math.min(1, Math.max(0, progress));
  if (clamped >= 1) return words.length - 1;

  const weights = words.map((w) => Math.max(1, w.length));
  const total = weights.reduce((sum, w) => sum + w, 0);
  let cursor = clamped * total;

  for (let i = 0; i < weights.length; i++) {
    if (cursor <= weights[i]) {
      return i + cursor / weights[i];
    }
    cursor -= weights[i];
  }
  return words.length - 1;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Single-line OBS caption strip: scrolls continuously with playback so the
 * spoken point stays slightly left-of-center. Soft edge mask. Blank until
 * speaking; fades out on the same clock as the speaking orb.
 */
export function ObsKaraokeCaptions({
  words,
  activeWordIndex,
  phase,
  captionText,
  speakingPlayback,
}: ObsKaraokeCaptionsProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const wordRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const [offsetX, setOffsetX] = useState(0);
  /** Last scroll offset while speaking — held through fade. */
  const frozenOffsetRef = useRef(0);

  const speaking = phase === "speaking" && words.length > 0;
  const [show, setShow] = useState(false);
  const [fading, setFading] = useState(false);

  // Same show/fade contract as ObsSpeakingPage (shared OBS_FADE_OUT_MS).
  useEffect(() => {
    if (speaking) {
      setShow(true);
      setFading(false);
      return;
    }
    if (!show) return;

    setFading(true);
    const id = window.setTimeout(() => {
      setShow(false);
      setFading(false);
    }, OBS_FADE_OUT_MS);
    return () => window.clearTimeout(id);
  }, [speaking, show]);

  useLayoutEffect(() => {
    if (!show) return;

    // During fade: keep the last speaking scroll position — never re-home.
    if (fading || !speaking) {
      setOffsetX(frozenOffsetRef.current);
      return;
    }

    const viewport = viewportRef.current;
    if (!viewport) return;

    const update = () => {
      const centers: number[] = [];
      for (let i = 0; i < words.length; i++) {
        const el = wordRefs.current[i];
        if (!el) return;
        centers.push(el.offsetLeft + el.offsetWidth / 2);
      }
      if (centers.length === 0) return;

      let progress = 0;
      if (speakingPlayback != null) {
        progress = captionProgressFromPlayback(
          speakingPlayback.playedMs,
          speakingPlayback.totalMs,
          captionText,
          speakingPlayback.totalFinal,
        );
      } else if (activeWordIndex > 0 && words.length > 1) {
        progress = activeWordIndex / (words.length - 1);
      }

      const frac = fractionalWordIndex(words, progress);
      const i0 = Math.floor(frac);
      const i1 = Math.min(i0 + 1, centers.length - 1);
      const t = frac - i0;
      const focusX = lerp(centers[i0], centers[i1], t);
      const next = viewport.clientWidth * FOCUS_RATIO - focusX;

      frozenOffsetRef.current = next;
      setOffsetX(next);
    };

    update();
    const ro = new ResizeObserver(update);
    ro.observe(viewport);
    return () => ro.disconnect();
  }, [
    show,
    fading,
    speaking,
    words,
    captionText,
    speakingPlayback,
    activeWordIndex,
  ]);

  if (!show || words.length === 0) return null;

  const trackClass = [
    "obs-karaoke__track",
    fading ? "obs-karaoke__track--fade-out" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="obs-karaoke" ref={viewportRef}>
      <p
        className={trackClass}
        style={{ transform: `translate3d(${offsetX}px, 0, 0)` }}
        aria-live="polite"
        aria-atomic="false"
      >
        {words.map((word, index) => {
          const spoken = index < activeWordIndex;
          const active = index === activeWordIndex && speaking && !fading;
          const className = [
            "obs-karaoke__word",
            spoken ? "obs-karaoke__word--spoken" : "",
            active ? "obs-karaoke__word--active" : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <span
              key={`${index}-${word}`}
              ref={(el) => {
                wordRefs.current[index] = el;
              }}
              className={className}
            >
              {word}
              {index < words.length - 1 ? "\u00A0" : ""}
            </span>
          );
        })}
      </p>
    </div>
  );
}

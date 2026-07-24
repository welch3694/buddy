import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CaptionPhase } from "../hooks/useCaptionHighlight";
import { OBS_FADE_OUT_MS } from "../obsTiming";

/** How many wrapped lines stay visible in the OBS window. */
const MAX_VISIBLE_LINES = 2;

type ObsRollingCaptionsProps = {
  words: string[];
  activeWordIndex: number;
  phase: CaptionPhase;
};

/** Assign each word a line index from post-layout ``offsetTop``. */
function lineIndexesForWords(wordEls: (HTMLElement | null)[]): number[] {
  const lines: number[] = [];
  let line = -1;
  let lastTop = Number.NaN;

  for (const el of wordEls) {
    if (!el) {
      lines.push(Math.max(0, line));
      continue;
    }
    const top = el.offsetTop;
    if (Number.isNaN(lastTop) || Math.abs(top - lastTop) > 2) {
      line += 1;
      lastTop = top;
    }
    lines.push(line);
  }
  return lines;
}

/**
 * Fixed-height rolling captions for OBS: word-wrap, keep N lines visible,
 * scroll older lines up as speech advances. Blank until speaking; fades with
 * the speaking orb.
 */
export function ObsRollingCaptions({
  words,
  activeWordIndex,
  phase,
}: ObsRollingCaptionsProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const wordRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const [offsetY, setOffsetY] = useState(0);
  const [viewportHeight, setViewportHeight] = useState<number | null>(null);
  /** Last scroll offset while speaking — held through fade. */
  const frozenOffsetRef = useRef(0);
  const frozenHeightRef = useRef<number | null>(null);

  const speaking = phase === "speaking" && words.length > 0;
  const [show, setShow] = useState(false);
  const [fading, setFading] = useState(false);

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

    if (fading || !speaking) {
      setOffsetY(frozenOffsetRef.current);
      if (frozenHeightRef.current != null) {
        setViewportHeight(frozenHeightRef.current);
      }
      return;
    }

    const viewport = viewportRef.current;
    if (!viewport) return;

    const update = () => {
      const els = wordRefs.current.slice(0, words.length);
      if (els.some((el) => el == null)) return;

      const lineOf = lineIndexesForWords(els);
      const focusIndex = Math.max(
        0,
        Math.min(activeWordIndex < 0 ? 0 : activeWordIndex, words.length - 1),
      );
      const activeLine = lineOf[focusIndex] ?? 0;
      const windowStart = Math.max(0, activeLine - MAX_VISIBLE_LINES + 1);
      const windowEnd = windowStart + MAX_VISIBLE_LINES - 1;

      const startEl = els.find((_, i) => lineOf[i] === windowStart);
      if (!startEl) return;

      // Measure the real ink box of the visible lines (includes descenders).
      let contentBottom = startEl.offsetTop;
      for (let i = 0; i < els.length; i++) {
        if (lineOf[i] < windowStart || lineOf[i] > windowEnd) continue;
        const el = els[i]!;
        contentBottom = Math.max(contentBottom, el.offsetTop + el.offsetHeight);
      }
      // Extra room for descenders + text-shadow that paints outside the glyph box.
      const DESCENDER_PAD_PX = 8;
      const height = contentBottom - startEl.offsetTop + DESCENDER_PAD_PX;
      const nextY = -startEl.offsetTop;

      frozenOffsetRef.current = nextY;
      frozenHeightRef.current = height;
      setOffsetY(nextY);
      setViewportHeight(height);
    };

    update();
    const ro = new ResizeObserver(update);
    ro.observe(viewport);
    return () => ro.disconnect();
  }, [show, fading, speaking, words, activeWordIndex]);

  if (!show || words.length === 0) return null;

  const trackClass = [
    "obs-rolling__track",
    fading ? "obs-rolling__track--fade-out" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className="obs-rolling"
      ref={viewportRef}
      style={viewportHeight != null ? { height: viewportHeight } : undefined}
    >
      <p
        className={trackClass}
        style={{ transform: `translate3d(0, ${offsetY}px, 0)` }}
        aria-live="polite"
        aria-atomic="false"
      >
        {words.map((word, index) => {
          const spoken = index < activeWordIndex;
          const active = index === activeWordIndex && speaking && !fading;
          const className = [
            "obs-rolling__word",
            spoken ? "obs-rolling__word--spoken" : "",
            active ? "obs-rolling__word--active" : "",
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
              {index < words.length - 1 ? " " : ""}
            </span>
          );
        })}
      </p>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import {
  estimateSpeakingDurationMs,
  SETTLE_CLEAR_MS,
  tokenizeCaption,
  wordIndexAtProgress,
} from "../utils/captionTiming";
import type { TurnState } from "../types/bridge";

export type CaptionPhase = "idle" | "buffering" | "speaking" | "settled";

export type CaptionHighlightState = {
  displayText: string;
  words: string[];
  activeWordIndex: number;
  phase: CaptionPhase;
};

/**
 * Drive caption visibility + estimated word highlight from turn state.
 * Text accumulates during generating; highlight runs while speaking;
 * settles then clears when returning to listening/holding.
 */
export function useCaptionHighlight(
  captionText: string,
  turnState: TurnState | null,
): CaptionHighlightState {
  const [displayText, setDisplayText] = useState("");
  const [activeWordIndex, setActiveWordIndex] = useState(-1);
  const [phase, setPhase] = useState<CaptionPhase>("idle");

  const bufferRef = useRef("");
  const phaseRef = useRef<CaptionPhase>("idle");
  const startMsRef = useRef<number | null>(null);
  const prevTurnRef = useRef<TurnState | null>(null);

  phaseRef.current = phase;

  // Accumulate caption text from bridge while generating/speaking
  useEffect(() => {
    if (!captionText) return;
    if (turnState !== "generating" && turnState !== "speaking") return;

    bufferRef.current = captionText;
    setDisplayText(captionText);

    if (turnState === "generating" && phaseRef.current !== "speaking") {
      setActiveWordIndex(-1);
      setPhase("buffering");
    }
  }, [captionText, turnState]);

  // Turn-state transitions
  useEffect(() => {
    const prev = prevTurnRef.current;
    prevTurnRef.current = turnState;

    if (turnState === "speaking") {
      const text = bufferRef.current;
      if (!text) return;

      setDisplayText(text);
      if (prev !== "speaking") {
        startMsRef.current = performance.now();
        setActiveWordIndex(0);
        setPhase("speaking");
      }
      return;
    }

    if (turnState === "listening" || turnState === "holding") {
      if (phaseRef.current === "idle") return;

      const text = bufferRef.current;
      const words = tokenizeCaption(text);
      if (text) setDisplayText(text);
      setActiveWordIndex(words.length > 0 ? words.length - 1 : -1);
      setPhase("settled");
      return;
    }

    if (
      turnState === "generating" &&
      prev &&
      prev !== "generating" &&
      (prev === "listening" || prev === "holding" || prev === "paused")
    ) {
      bufferRef.current = captionText || "";
      setDisplayText(captionText || "");
      setActiveWordIndex(-1);
      setPhase(captionText ? "buffering" : "idle");
      startMsRef.current = null;
    }
  }, [turnState, captionText]);

  // rAF word highlight while speaking
  useEffect(() => {
    if (phase !== "speaking") return;

    const text = bufferRef.current || displayText;
    const words = tokenizeCaption(text);
    if (words.length === 0) return;

    const duration = estimateSpeakingDurationMs(text);
    if (startMsRef.current == null) {
      startMsRef.current = performance.now();
    }

    let raf = 0;
    const tick = (now: number) => {
      const start = startMsRef.current ?? now;
      const progress = Math.min(1, (now - start) / duration);
      setActiveWordIndex(wordIndexAtProgress(words, progress));
      if (progress < 1) {
        raf = requestAnimationFrame(tick);
      }
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [phase, displayText]);

  // Clear after settle window (playback complete / return to listening)
  useEffect(() => {
    if (phase !== "settled") return;
    if (turnState !== "listening" && turnState !== "holding") return;

    const id = window.setTimeout(() => {
      bufferRef.current = "";
      setDisplayText("");
      setActiveWordIndex(-1);
      setPhase("idle");
      startMsRef.current = null;
    }, SETTLE_CLEAR_MS);

    return () => window.clearTimeout(id);
  }, [phase, turnState]);

  const words = tokenizeCaption(displayText);
  return {
    displayText,
    words,
    activeWordIndex,
    phase,
  };
}

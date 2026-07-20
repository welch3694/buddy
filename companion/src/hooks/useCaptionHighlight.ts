import { useEffect, useRef, useState } from "react";
import {
  captionProgressFromPlayback,
  SETTLE_CLEAR_MS,
  tokenizeCaption,
  wordIndexAtProgress,
} from "../utils/captionTiming";
import type { SpeakingPlayback, TurnState } from "../types/bridge";

export type CaptionPhase = "idle" | "buffering" | "speaking" | "settled";

export type CaptionHighlightState = {
  displayText: string;
  words: string[];
  activeWordIndex: number;
  phase: CaptionPhase;
};

/**
 * Drive caption visibility + word highlight from turn state.
 * Uses PCM played/enqueued ms floored by a text-duration estimate so highlight
 * does not race ahead when the playhead catches the TTS frontier early.
 */
export function useCaptionHighlight(
  captionText: string,
  turnState: TurnState | null,
  speakingPlayback: SpeakingPlayback | null = null,
): CaptionHighlightState {
  const [displayText, setDisplayText] = useState("");
  const [activeWordIndex, setActiveWordIndex] = useState(-1);
  const [phase, setPhase] = useState<CaptionPhase>("idle");

  const bufferRef = useRef("");
  const phaseRef = useRef<CaptionPhase>("idle");
  const prevTurnRef = useRef<TurnState | null>(null);
  const progressPeakRef = useRef(0);

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
        progressPeakRef.current = 0;
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
      progressPeakRef.current = 0;
    }
  }, [turnState, captionText]);

  // PCM playback → word highlight (estimate-floored progress)
  useEffect(() => {
    if (phase !== "speaking") return;

    const text = bufferRef.current || displayText;
    const words = tokenizeCaption(text);
    if (words.length === 0) return;

    // Hold first word until audible samples arrive (avoid TTFA race).
    if (speakingPlayback == null) {
      setActiveWordIndex(0);
      return;
    }

    const raw = captionProgressFromPlayback(
      speakingPlayback.playedMs,
      speakingPlayback.totalMs,
      text,
      speakingPlayback.totalFinal,
    );
    // Monotonic on the *floored* progress only — prevents tiny dips when
    // enqueued audio grows, without locking to a premature raw ratio spike.
    progressPeakRef.current = Math.max(progressPeakRef.current, raw);
    setActiveWordIndex(wordIndexAtProgress(words, progressPeakRef.current));
  }, [phase, displayText, speakingPlayback]);

  // Clear after settle window (playback complete / return to listening)
  useEffect(() => {
    if (phase !== "settled") return;
    if (turnState !== "listening" && turnState !== "holding") return;

    const id = window.setTimeout(() => {
      bufferRef.current = "";
      setDisplayText("");
      setActiveWordIndex(-1);
      setPhase("idle");
      progressPeakRef.current = 0;
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

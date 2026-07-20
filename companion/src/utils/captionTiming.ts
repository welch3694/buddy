/** ~180 WPM equivalent for estimate fallback (no TTS word timings). */
export const MS_PER_CHAR = 55;
export const MIN_SPEAKING_MS = 800;
export const SETTLE_CLEAR_MS = 1400;

export function tokenizeCaption(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  return trimmed.split(/\s+/);
}

export function estimateSpeakingDurationMs(text: string): number {
  const length = text.trim().length;
  if (length === 0) return MIN_SPEAKING_MS;
  return Math.max(MIN_SPEAKING_MS, length * MS_PER_CHAR);
}

/**
 * Map playback samples onto full caption text.
 *
 * While TTS is still filling the queue, floor the denominator with a
 * text-length estimate so ``played/enqueued`` cannot race to 1.0 at the
 * synth frontier. Once ``totalFinal`` is set (RESPONSE_DONE enqueued), trust
 * the real audio length so the last words track remaining PCM instead of
 * snapping when playback ends.
 */
export function captionProgressFromPlayback(
  playedMs: number,
  enqueuedMs: number,
  captionText: string,
  totalFinal: boolean = false,
): number {
  const played = Math.max(0, playedMs);
  const enqueued = Math.max(0, enqueuedMs);

  if (totalFinal) {
    const total = Math.max(enqueued, played, 1);
    return Math.min(1, played / total);
  }

  const estimate = estimateSpeakingDurationMs(captionText);
  const total = Math.max(enqueued, estimate, played, 1);
  return Math.min(1, played / total);
}

/**
 * Map 0..1 progress across words weighted by character length
 * so longer words hold the highlight longer.
 */
export function wordIndexAtProgress(words: string[], progress: number): number {
  if (words.length === 0) return -1;
  const clamped = Math.min(1, Math.max(0, progress));
  if (clamped >= 1) return words.length - 1;

  const weights = words.map((w) => Math.max(1, w.length));
  const total = weights.reduce((sum, w) => sum + w, 0);
  let cursor = clamped * total;

  for (let i = 0; i < weights.length; i++) {
    cursor -= weights[i];
    if (cursor < 0) return i;
  }
  return words.length - 1;
}

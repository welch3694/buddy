"""Track local PCM playback for companion caption sync.

Wraps ``LocalAudioStreamer.output_queue`` put/get so we can emit
``speaking_progress`` from audible samples (not TTS start / WPM estimates).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from speech_to_speech.pipeline.messages import AUDIO_RESPONSE_DONE

DEFAULT_SAMPLE_RATE = 16000
# Audio callback is ~32ms; keep bridge traffic modest.
MIN_EMIT_INTERVAL_S = 0.05


def _sample_count(item: Any) -> int:
    if isinstance(item, np.ndarray):
        if item.ndim == 0:
            return 0
        return int(item.shape[0])
    return 0


class PlaybackProgressTracker:
    """Count enqueued vs played samples for the current assistant response."""

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        min_emit_interval_s: float = MIN_EMIT_INTERVAL_S,
        emit_fn: Any | None = None,
    ) -> None:
        self.sample_rate = max(1, sample_rate)
        self.min_emit_interval_s = max(0.0, min_emit_interval_s)
        self._emit_fn = emit_fn
        self._lock = threading.Lock()
        self.enqueued_samples = 0
        self.played_samples = 0
        self.total_final = False
        self._last_emit_monotonic = 0.0
        self._last_progress = -1.0

    def reset(self) -> None:
        with self._lock:
            self.enqueued_samples = 0
            self.played_samples = 0
            self.total_final = False
            self._last_emit_monotonic = 0.0
            self._last_progress = -1.0

    def on_enqueue(self, item: Any) -> None:
        if isinstance(item, np.ndarray):
            samples = _sample_count(item)
            if samples <= 0:
                return
            with self._lock:
                self.enqueued_samples += samples
            return

        if item == AUDIO_RESPONSE_DONE:
            # TTS finished — lock denominator to real audio length now, while
            # remaining PCM may still be playing out of the queue.
            with self._lock:
                self.total_final = True
            self._emit(force=True, complete=False)
            return

    def on_dequeue(self, item: Any) -> None:
        if isinstance(item, np.ndarray):
            samples = _sample_count(item)
            if samples <= 0:
                return
            with self._lock:
                self.played_samples += samples
            self._emit(force=False, complete=False)
            return

        if item == AUDIO_RESPONSE_DONE:
            self._emit(force=True, complete=True)
            self.reset()

    def snapshot(self) -> dict[str, float | int | bool]:
        with self._lock:
            played = self.played_samples
            total = max(self.enqueued_samples, played)
            total_final = self.total_final
        played_ms = int(round(1000.0 * played / self.sample_rate))
        total_ms = int(round(1000.0 * total / self.sample_rate)) if total else 0
        progress = (played / total) if total > 0 else 0.0
        return {
            "played_ms": played_ms,
            "total_ms": total_ms,
            "progress": min(1.0, max(0.0, progress)),
            "total_final": total_final,
        }

    def _emit(self, *, force: bool, complete: bool) -> None:
        emit = self._emit_fn
        if emit is None:
            from buddy_tools.companion.publisher import emit_speaking_progress

            emit = emit_speaking_progress

        now = time.monotonic()
        snap = self.snapshot()
        if complete:
            snap["progress"] = 1.0
            snap["total_final"] = True
            if snap["total_ms"] < snap["played_ms"]:
                snap["total_ms"] = snap["played_ms"]

        progress = float(snap["progress"])
        with self._lock:
            if not force and (now - self._last_emit_monotonic) < self.min_emit_interval_s:
                return
            # Skip near-duplicates unless completing / finalizing the utterance.
            if (
                not force
                and self._last_progress >= 0
                and abs(progress - self._last_progress) < 0.005
            ):
                return
            self._last_emit_monotonic = now
            self._last_progress = progress

        try:
            emit(
                progress=float(snap["progress"]),
                played_ms=int(snap["played_ms"]),
                total_ms=int(snap["total_ms"]),
                total_final=bool(snap["total_final"]),
            )
        except Exception:
            # Never break the audio callback.
            return


def install_playback_progress_tracking(
    streamer: Any,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> PlaybackProgressTracker | None:
    """Wrap ``streamer.output_queue`` put/get to track audible playback progress."""
    if streamer is None:
        return None
    if getattr(streamer, "_buddy_playback_progress_installed", False):
        return getattr(streamer, "_buddy_playback_tracker", None)

    output_queue = getattr(streamer, "output_queue", None)
    if output_queue is None:
        return None

    tracker = PlaybackProgressTracker(sample_rate=sample_rate)
    original_put = output_queue.put
    original_get_nowait = output_queue.get_nowait

    def put(item: Any, *args: Any, **kwargs: Any) -> None:
        # put_nowait delegates to put — wrap only put to avoid double-counting.
        tracker.on_enqueue(item)
        original_put(item, *args, **kwargs)

    def get_nowait() -> Any:
        item = original_get_nowait()
        tracker.on_dequeue(item)
        return item

    output_queue.put = put  # type: ignore[method-assign]
    output_queue.get_nowait = get_nowait  # type: ignore[method-assign]

    streamer._buddy_playback_progress_installed = True
    streamer._buddy_playback_tracker = tracker
    return tracker

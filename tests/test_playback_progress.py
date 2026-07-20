"""Tests for companion playback progress tracking."""

from __future__ import annotations

import unittest
from queue import Empty, Queue

import numpy as np

from speech_to_speech.pipeline.messages import AUDIO_RESPONSE_DONE

from buddy_tools.companion.playback_progress import (
    PlaybackProgressTracker,
    install_playback_progress_tracking,
)
from buddy_tools.companion.publisher import (
    CompanionEventPublisher,
    emit_speaking_progress,
    reset_companion_publisher_for_tests,
    set_companion_publisher,
)


class PlaybackProgressTrackerTests(unittest.TestCase):
    def test_progress_tracks_enqueued_and_played_samples(self) -> None:
        emitted: list[dict[str, float | int | bool]] = []

        def emit_fn(
            *,
            progress: float,
            played_ms: int,
            total_ms: int,
            total_final: bool = False,
        ) -> None:
            emitted.append(
                {
                    "progress": progress,
                    "played_ms": played_ms,
                    "total_ms": total_ms,
                    "total_final": total_final,
                }
            )

        tracker = PlaybackProgressTracker(
            sample_rate=16000,
            min_emit_interval_s=0.0,
            emit_fn=emit_fn,
        )
        chunk = np.zeros(1600, dtype=np.int16)  # 100 ms
        tracker.on_enqueue(chunk)
        tracker.on_enqueue(chunk)
        tracker.on_dequeue(chunk)

        self.assertEqual(len(emitted), 1)
        self.assertAlmostEqual(float(emitted[0]["progress"]), 0.5, places=3)
        self.assertEqual(emitted[0]["played_ms"], 100)
        self.assertEqual(emitted[0]["total_ms"], 200)
        self.assertFalse(emitted[0]["total_final"])

        tracker.on_dequeue(chunk)
        self.assertAlmostEqual(float(emitted[-1]["progress"]), 1.0, places=3)

    def test_done_enqueued_marks_total_final_before_playback_ends(self) -> None:
        emitted: list[dict[str, float | int | bool]] = []

        def emit_fn(
            *,
            progress: float,
            played_ms: int,
            total_ms: int,
            total_final: bool = False,
        ) -> None:
            emitted.append(
                {
                    "progress": progress,
                    "played_ms": played_ms,
                    "total_ms": total_ms,
                    "total_final": total_final,
                }
            )

        tracker = PlaybackProgressTracker(
            sample_rate=16000,
            min_emit_interval_s=0.0,
            emit_fn=emit_fn,
        )
        chunk_a = np.zeros(1600, dtype=np.int16)
        chunk_b = np.zeros(1600, dtype=np.int16)
        tracker.on_enqueue(chunk_a)
        tracker.on_enqueue(chunk_b)
        tracker.on_dequeue(chunk_a)
        tracker.on_enqueue(AUDIO_RESPONSE_DONE)

        self.assertTrue(tracker.total_final)
        self.assertTrue(emitted[-1]["total_final"])
        self.assertAlmostEqual(float(emitted[-1]["progress"]), 0.5, places=3)

        tracker.on_dequeue(chunk_b)
        self.assertTrue(emitted[-1]["total_final"])
        self.assertAlmostEqual(float(emitted[-1]["progress"]), 1.0, places=3)

        tracker.on_dequeue(AUDIO_RESPONSE_DONE)
        self.assertEqual(emitted[-1]["progress"], 1.0)
        self.assertEqual(tracker.played_samples, 0)
        self.assertFalse(tracker.total_final)

    def test_install_wraps_output_queue(self) -> None:
        emitted: list[dict[str, float | int | bool]] = []

        def emit_fn(
            *,
            progress: float,
            played_ms: int,
            total_ms: int,
            total_final: bool = False,
        ) -> None:
            emitted.append({"progress": progress, "total_final": total_final})

        class FakeStreamer:
            def __init__(self) -> None:
                self.output_queue: Queue = Queue()

        streamer = FakeStreamer()
        tracker = install_playback_progress_tracking(streamer)
        self.assertIsNotNone(tracker)
        assert tracker is not None
        tracker.min_emit_interval_s = 0.0
        tracker._emit_fn = emit_fn

        chunk = np.zeros(1600, dtype=np.int16)
        streamer.output_queue.put_nowait(chunk)
        streamer.output_queue.put_nowait(AUDIO_RESPONSE_DONE)
        self.assertTrue(any(e["total_final"] for e in emitted))

        out = streamer.output_queue.get_nowait()
        self.assertIsInstance(out, np.ndarray)
        self.assertGreater(len(emitted), 0)

        done = streamer.output_queue.get_nowait()
        self.assertEqual(done, AUDIO_RESPONSE_DONE)
        self.assertEqual(emitted[-1]["progress"], 1.0)

        with self.assertRaises(Empty):
            streamer.output_queue.get_nowait()

        again = install_playback_progress_tracking(streamer)
        self.assertIs(again, tracker)


class SpeakingProgressEmitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_companion_publisher_for_tests()

    def tearDown(self) -> None:
        reset_companion_publisher_for_tests()

    def test_emit_speaking_progress(self) -> None:
        publisher = CompanionEventPublisher()
        set_companion_publisher(publisher)
        emit_speaking_progress(
            progress=0.25,
            played_ms=500,
            total_ms=2000,
            total_final=True,
        )
        events = publisher.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "speaking_progress")
        self.assertEqual(events[0]["progress"], 0.25)
        self.assertEqual(events[0]["played_ms"], 500)
        self.assertEqual(events[0]["total_ms"], 2000)
        self.assertTrue(events[0]["total_final"])

    def test_emit_clamps_progress(self) -> None:
        publisher = CompanionEventPublisher()
        set_companion_publisher(publisher)
        publisher.emit_speaking_progress(progress=1.5, played_ms=10, total_ms=10)
        events = publisher.drain()
        self.assertEqual(events[0]["progress"], 1.0)
        self.assertFalse(events[0]["total_final"])

    def test_emit_no_op_without_publisher(self) -> None:
        reset_companion_publisher_for_tests()
        emit_speaking_progress(progress=0.1, played_ms=1, total_ms=10)


if __name__ == "__main__":
    unittest.main()

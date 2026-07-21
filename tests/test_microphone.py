"""Tests for default-microphone follow / stream reload (#155)."""

from __future__ import annotations

import threading
import time
import unittest
from queue import Queue
from typing import Any
from unittest.mock import MagicMock, patch

from buddy_tools.voice.microphone import (
    MicDeviceWatcher,
    default_input_fingerprint,
    describe_device,
    refresh_portaudio_devices,
    reset_mic_device_watcher_for_tests,
    resolve_duplex_device,
    run_local_audio_with_reload,
    wait_for_playback_drain,
)


class _FakeSd:
    """Minimal sounddevice stand-in for unit tests."""

    def __init__(self) -> None:
        self.default = MagicMock()
        self.default.device = [1, 5]
        self._devices = {
            1: {"name": "Mic A", "hostapi": 0},
            2: {"name": "Mic B", "hostapi": 0},
            5: {"name": "Speakers", "hostapi": 0},
        }
        self.stream_calls: list[dict[str, Any]] = []
        self._terminate_calls = 0
        self._initialize_calls = 0

    def query_devices(self, index: int | None = None, kind: str | None = None) -> Any:
        if index is None:
            return list(self._devices.values())
        return self._devices[int(index)]

    def Stream(self, **kwargs: Any) -> Any:
        self.stream_calls.append(kwargs)
        return _FakeStreamContext()

    def _terminate(self) -> None:
        self._terminate_calls += 1

    def _initialize(self) -> None:
        self._initialize_calls += 1


class _FakeStreamContext:
    def __enter__(self) -> _FakeStreamContext:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class FingerprintTests(unittest.TestCase):
    def test_default_input_fingerprint(self) -> None:
        sd = _FakeSd()
        self.assertEqual(default_input_fingerprint(sd), (1, "Mic A", 0))

    def test_resolve_duplex_device(self) -> None:
        sd = _FakeSd()
        self.assertEqual(resolve_duplex_device(sd), (1, 5))

    def test_describe_device(self) -> None:
        sd = _FakeSd()
        self.assertEqual(describe_device(1, sd), "1:Mic A")

    def test_refresh_portaudio_devices(self) -> None:
        sd = _FakeSd()
        refresh_portaudio_devices(sd)
        self.assertEqual(sd._terminate_calls, 1)
        self.assertEqual(sd._initialize_calls, 1)


class PlaybackDrainTests(unittest.TestCase):
    def test_wait_returns_when_empty(self) -> None:
        q: Queue[Any] = Queue()
        started = time.monotonic()
        wait_for_playback_drain(q, timeout_s=0.5, poll_s=0.01)
        self.assertLess(time.monotonic() - started, 0.2)

    def test_wait_times_out_when_nonempty(self) -> None:
        q: Queue[Any] = Queue()
        q.put(b"chunk")
        started = time.monotonic()
        wait_for_playback_drain(q, timeout_s=0.05, poll_s=0.01)
        self.assertGreaterEqual(time.monotonic() - started, 0.04)


class MicDeviceWatcherTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_mic_device_watcher_for_tests()

    def test_baseline_does_not_reload(self) -> None:
        fingerprints = [(1, "Mic A", 0)]
        reloads = 0

        def fingerprint_fn() -> tuple[int, str, int]:
            return fingerprints[-1]

        def on_reload() -> None:
            nonlocal reloads
            reloads += 1

        watcher = MicDeviceWatcher(poll_interval_s=0.05, fingerprint_fn=fingerprint_fn)
        watcher.start(on_reload)
        time.sleep(0.18)
        watcher.stop()
        self.assertEqual(reloads, 0)

    def test_fingerprint_change_requests_reload(self) -> None:
        fingerprints = [(1, "Mic A", 0)]
        reloads = threading.Event()

        def fingerprint_fn() -> tuple[int, str, int]:
            return fingerprints[-1]

        def on_reload() -> None:
            reloads.set()

        watcher = MicDeviceWatcher(poll_interval_s=0.05, fingerprint_fn=fingerprint_fn)
        watcher.start(on_reload)
        time.sleep(0.12)
        fingerprints.append((2, "Mic B", 0))
        self.assertTrue(reloads.wait(timeout=1.0))
        watcher.stop()


class RunLocalAudioReloadTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_mic_device_watcher_for_tests()

    def test_reopens_stream_with_updated_device(self) -> None:
        sd = _FakeSd()
        stop_event = threading.Event()
        reload_event = threading.Event()
        streamer = MagicMock()
        streamer.stop_event = stop_event
        streamer.reload_event = reload_event
        streamer.list_play_chunk_size = 512
        streamer.input_queue = Queue()
        streamer.output_queue = Queue()
        streamer.should_listen = threading.Event()

        devices = [[1, 5], [2, 5]]

        def resolve_side_effect(_sd: Any = None) -> tuple[int, int]:
            pair = devices[0] if devices else [2, 5]
            return int(pair[0]), int(pair[1])

        def refresh_side_effect(_sd: Any = None) -> None:
            if len(devices) > 1:
                devices.pop(0)

        def run_loop() -> None:
            with (
                patch(
                    "buddy_tools.voice.microphone.resolve_duplex_device",
                    side_effect=resolve_side_effect,
                ),
                patch(
                    "buddy_tools.voice.microphone.refresh_portaudio_devices",
                    side_effect=refresh_side_effect,
                ),
                patch(
                    "buddy_tools.voice.microphone.wait_for_playback_drain",
                ),
                patch(
                    "buddy_tools.voice.microphone.get_mic_device_watcher",
                ) as get_watcher,
            ):
                watcher = MagicMock()
                get_watcher.return_value = watcher
                # Trigger one reload shortly after first stream opens, then stop.
                def start_and_reload(on_reload: Any) -> None:
                    def _fire() -> None:
                        time.sleep(0.05)
                        on_reload()
                        time.sleep(0.08)
                        stop_event.set()

                    threading.Thread(target=_fire, daemon=True).start()

                watcher.start.side_effect = start_and_reload
                run_local_audio_with_reload(streamer, sd=sd)

        run_loop()
        self.assertGreaterEqual(len(sd.stream_calls), 2)
        self.assertEqual(sd.stream_calls[0]["device"], (1, 5))
        self.assertEqual(sd.stream_calls[1]["device"], (2, 5))
        self.assertEqual(sd.stream_calls[0]["samplerate"], 16000)
        self.assertEqual(sd.stream_calls[0]["channels"], 1)


if __name__ == "__main__":
    unittest.main()

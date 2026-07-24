"""Follow OS default microphone changes without restarting the process.

PortAudio binds a duplex stream to device indices at open time and caches the
default device list while a stream is open. On Windows we therefore fingerprint
the default capture endpoint via Core Audio (not PortAudio) so default-mic
swaps and hotplug promotions are visible without tearing down the live stream
first. When the fingerprint changes we close, refresh PortAudio, and reopen.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 1.0
_PLAYBACK_DRAIN_TIMEOUT_S = 0.5
_PLAYBACK_DRAIN_POLL_S = 0.05

# Fingerprint: (source, key, label) — key is what must change to trigger reload.
InputFingerprint = tuple[str, str, str]

ReloadCallback = Callable[[], None]


def windows_default_capture_fingerprint() -> InputFingerprint:
    """Return the Windows Core Audio default capture endpoint id + name.

    Independent of PortAudio, so it updates while a sounddevice stream is open.
    """
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities, IMMDeviceEnumerator

    enumerator = comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator,
        IMMDeviceEnumerator,
        CLSCTX_ALL,
    )
    device = enumerator.GetDefaultAudioEndpoint(
        EDataFlow.eCapture.value,
        ERole.eMultimedia.value,
    )
    info = AudioUtilities.CreateDevice(device)
    endpoint_id = str(info.id or "")
    name = str(info.FriendlyName or "")
    if not endpoint_id:
        raise RuntimeError("Windows default capture endpoint id is empty")
    return ("win", endpoint_id, name)


def portaudio_default_input_fingerprint(sd: Any | None = None) -> InputFingerprint:
    """Return PortAudio default input fingerprint (fallback / non-Windows)."""
    if sd is None:
        import sounddevice as sd

    index = int(sd.default.device[0])
    info = sd.query_devices(index)
    name = str(info.get("name", "") or "")
    hostapi = int(info.get("hostapi", -1))
    return ("pa", f"{index}:{hostapi}:{name}", name)


def default_input_fingerprint(sd: Any | None = None) -> InputFingerprint:
    """Fingerprint the OS default input device for change detection."""
    if sys.platform == "win32":
        try:
            return windows_default_capture_fingerprint()
        except Exception:
            logger.debug(
                "Windows Core Audio default-mic probe failed; falling back to PortAudio",
                exc_info=True,
            )
    return portaudio_default_input_fingerprint(sd)


def resolve_duplex_device(sd: Any | None = None) -> tuple[int, int]:
    """Return ``(input_index, output_index)`` from current PortAudio defaults."""
    if sd is None:
        import sounddevice as sd

    devices = sd.default.device
    return int(devices[0]), int(devices[1])


def refresh_portaudio_devices(sd: Any | None = None) -> None:
    """Re-initialize PortAudio so hotplug devices appear in the device list.

    Must only be called while no ``sounddevice`` streams are open.
    """
    if sd is None:
        import sounddevice as sd

    try:
        sd._terminate()
    except Exception:
        logger.debug("PortAudio terminate during device refresh failed", exc_info=True)
    try:
        sd._initialize()
    except Exception:
        logger.exception("PortAudio initialize during device refresh failed")
        raise


def wait_for_playback_drain(
    output_queue: Any,
    *,
    timeout_s: float = _PLAYBACK_DRAIN_TIMEOUT_S,
    poll_s: float = _PLAYBACK_DRAIN_POLL_S,
) -> None:
    """Briefly wait for TTS chunks to finish before reopening the stream."""
    if output_queue is None:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if output_queue.empty():
                return
        except Exception:
            return
        time.sleep(poll_s)


def describe_device(index: int, sd: Any | None = None) -> str:
    """Human-readable device label for logs."""
    if sd is None:
        import sounddevice as sd

    try:
        info = sd.query_devices(index)
        name = str(info.get("name", "") or f"device {index}")
        return f"{index}:{name}"
    except Exception:
        return f"{index}:?"


def _fingerprint_label(fingerprint: InputFingerprint) -> str:
    _source, key, name = fingerprint
    return name or key


class MicDeviceWatcher:
    """Poll the OS default input device and request a stream reload on change."""

    def __init__(
        self,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        fingerprint_fn: Callable[[], InputFingerprint] | None = None,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._fingerprint_fn = fingerprint_fn or default_input_fingerprint
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_reload: ReloadCallback | None = None
        self._last_fingerprint: InputFingerprint | None = None

    def start(self, on_reload: ReloadCallback) -> None:
        with self._lock:
            self._on_reload = on_reload
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._last_fingerprint = None
            self._thread = threading.Thread(
                target=self._run,
                name="buddy-mic-device-watcher",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._poll_interval_s + 0.5)
        with self._lock:
            self._on_reload = None
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval_s):
            try:
                fingerprint = self._fingerprint_fn()
            except Exception:
                logger.debug("Mic device fingerprint poll failed", exc_info=True)
                continue

            if self._last_fingerprint is None:
                self._last_fingerprint = fingerprint
                logger.info(
                    "Mic watcher baseline input device: %s (%s)",
                    _fingerprint_label(fingerprint),
                    fingerprint[0],
                )
                continue

            # Compare stable key only (source + id); label may be truncated elsewhere.
            if (
                fingerprint[0] == self._last_fingerprint[0]
                and fingerprint[1] == self._last_fingerprint[1]
            ):
                continue

            old = self._last_fingerprint
            self._last_fingerprint = fingerprint
            logger.info(
                "Default microphone changed: %s -> %s; requesting stream reload",
                _fingerprint_label(old),
                _fingerprint_label(fingerprint),
            )
            with self._lock:
                callback = self._on_reload
            if callback is not None:
                try:
                    callback()
                except Exception:
                    logger.exception("Mic reload callback failed")


_watcher: MicDeviceWatcher | None = None
_watcher_lock = threading.Lock()


def get_mic_device_watcher() -> MicDeviceWatcher:
    """Process-wide watcher singleton."""
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = MicDeviceWatcher()
        return _watcher


def reset_mic_device_watcher_for_tests() -> None:
    """Stop and clear the singleton (tests only)."""
    global _watcher
    with _watcher_lock:
        if _watcher is not None:
            _watcher.stop()
        _watcher = None


def fill_duplex_audio_frame(
    *,
    indata: Any,
    outdata: Any,
    input_queue: Any,
    output_queue: Any,
    should_listen: Any,
    dither: Any,
    audio_response_done: Any,
    np: Any,
) -> None:
    """Always capture mic PCM; play TTS when queued, otherwise dither.

    Upstream LocalAudioStreamer is half-duplex (drops mic while playing). Buddy
    captures during playback so keyword barge-in can hear the user over TTS.
    """
    pcm = np.ascontiguousarray(indata, dtype=np.int16)
    input_queue.put(pcm.tobytes())

    if output_queue.empty():
        outdata[:] = dither
        return

    try:
        audio_chunk = output_queue.get_nowait()
        if isinstance(audio_chunk, np.ndarray):
            outdata[:] = audio_chunk[:, np.newaxis]
        elif audio_chunk == audio_response_done:
            should_listen.set()
            logger.debug("Response complete, listening re-enabled")
            outdata[:] = 0 * outdata
        else:
            outdata[:] = 0 * outdata
    except Exception:
        outdata[:] = 0 * outdata


def run_local_audio_with_reload(streamer: Any, *, sd: Any | None = None) -> None:
    """Run duplex capture/playback, reopening when the default mic changes.

    Replaces upstream ``LocalAudioStreamer.run`` so Buddy can follow OS default
    device changes without a process restart. Unlike upstream, mic capture
    continues while TTS is playing (required for keyword barge-in).
    """
    import numpy as np

    if sd is None:
        import sounddevice as sd

    from speech_to_speech.pipeline.messages import AUDIO_RESPONSE_DONE

    reload_event = getattr(streamer, "reload_event", None)
    if reload_event is None:
        reload_event = threading.Event()
        streamer.reload_event = reload_event

    dither = np.random.randint(-1, 2, size=(streamer.list_play_chunk_size, 1), dtype=np.int16)
    bad_status_streak = 0

    def request_reload() -> None:
        if streamer.stop_event.is_set():
            return
        reload_event.set()

    def callback(indata: np.ndarray, outdata: np.ndarray, frames: int, time_info: float, status: Any) -> None:
        nonlocal bad_status_streak
        if streamer.stop_event.is_set():
            outdata[:] = 0 * outdata
            return

        # Device removal / host abort often surfaces as a sticky non-zero status.
        if status:
            bad_status_streak += 1
            if bad_status_streak >= 5:
                logger.warning(
                    "Audio callback status=%s; requesting mic stream reload",
                    status,
                )
                request_reload()
                bad_status_streak = 0
        else:
            bad_status_streak = 0

        fill_duplex_audio_frame(
            indata=indata,
            outdata=outdata,
            input_queue=streamer.input_queue,
            output_queue=streamer.output_queue,
            should_listen=streamer.should_listen,
            dither=dither,
            audio_response_done=AUDIO_RESPONSE_DONE,
            np=np,
        )

    watcher = get_mic_device_watcher()
    watcher.start(request_reload)
    try:
        logger.debug("Available devices:\n%s", sd.query_devices())
        while not streamer.stop_event.is_set():
            in_idx, out_idx = resolve_duplex_device(sd)
            logger.info(
                "Starting local audio stream on input=%s output=%s",
                describe_device(in_idx, sd),
                describe_device(out_idx, sd),
            )
            reload_event.clear()
            bad_status_streak = 0
            try:
                with sd.Stream(
                    samplerate=16000,
                    dtype="int16",
                    channels=1,
                    callback=callback,
                    blocksize=streamer.list_play_chunk_size,
                    device=(in_idx, out_idx),
                ):
                    while not streamer.stop_event.is_set() and not reload_event.is_set():
                        time.sleep(0.001)
            except Exception:
                if streamer.stop_event.is_set():
                    break
                logger.exception(
                    "Local audio stream failed; refreshing devices and reopening"
                )
                reload_event.set()

            if streamer.stop_event.is_set():
                break

            wait_for_playback_drain(getattr(streamer, "output_queue", None))
            old_label = describe_device(in_idx, sd)
            try:
                refresh_portaudio_devices(sd)
            except Exception:
                logger.exception(
                    "Failed to refresh PortAudio after mic change; retrying reopen"
                )
            new_in, new_out = resolve_duplex_device(sd)
            logger.info(
                "Reopened local audio stream after mic change: input %s -> %s (output %s)",
                old_label,
                describe_device(new_in, sd),
                describe_device(new_out, sd),
            )
    finally:
        watcher.stop()
        logger.info("Stopping local audio stream")

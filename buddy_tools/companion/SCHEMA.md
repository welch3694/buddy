# Companion status bridge — event schema

Localhost WebSocket publisher for the sci-fi companion panel (#115 / epic #101).

## Enable

```env
BUDDY_COMPANION_BRIDGE=1
# optional:
# BUDDY_COMPANION_BRIDGE_HOST=127.0.0.1
# BUDDY_COMPANION_BRIDGE_PORT=8766
```

URL: `ws://127.0.0.1:8766`

Bind is loopback-only by default. The bridge starts from speech-to-speech bootstrap when the env flag is set. Safe with zero clients: the event queue is always drained.

## Events

All frames are JSON objects with a `type` discriminator and ISO-8601 `ts` (UTC).

### `turn_state`

Emitted on `TurnStateController` transitions.

```json
{
  "type": "turn_state",
  "state": "listening",
  "reason": "playback_complete",
  "turn_id": "optional",
  "turn_revision": 0,
  "ts": "2026-07-19T16:00:00+00:00"
}
```

`state` is one of: `listening` | `holding` | `generating` | `speaking` | `paused`.

### `persona`

Active personality for the voice session. Emitted once at bridge start and included in connect snapshots.

```json
{
  "type": "persona",
  "id": "coach",
  "name": "Coach",
  "memory_namespace": "coach",
  "voice_id": "ron",
  "ts": "2026-07-19T16:00:00+00:00"
}
```

`voice_id` may be omitted. Re-emitted when the voice session switches personality mid-run; connect snapshots always reflect the latest.

### `assistant_text`

Caption chunks for what Buddy is saying (post pulse-suppress filter).

```json
{
  "type": "assistant_text",
  "text": "Hello there",
  "turn_id": "optional",
  "turn_revision": 0,
  "ts": "2026-07-19T16:00:00+00:00"
}
```

### `speaking_progress`

Audible PCM playback progress from the local audio streamer (sample counts at 16 kHz). Used by live captions for word highlight. Not included in connect snapshots.

```json
{
  "type": "speaking_progress",
  "progress": 0.42,
  "played_ms": 1260,
  "total_ms": 3000,
  "total_final": false,
  "ts": "2026-07-19T16:00:00+00:00"
}
```

`progress` is `played / max(enqueued, played)` for the current response. `total_ms` grows as TTS fills the output queue. `total_final` flips to `true` when `AUDIO_RESPONSE_DONE` is **enqueued** (synth finished; total audio length locked) — the companion then drops the text-duration floor and tracks remaining PCM through the last words. Emits ~20 Hz max while audio is playing; forces `progress: 1` when the done sentinel is **played**.

### `pulse_state`

Salient snapshot from `{memory}/{persona}/pulse_state.json` (polled ~0.5s; change-only).

Inactive:

```json
{ "type": "pulse_state", "active": false, "ts": "…" }
```

Active (fields may be null):

```json
{
  "type": "pulse_state",
  "active": true,
  "skill_name": "live-director",
  "status": "active",
  "phase": "running",
  "pulse_mode": "directed",
  "pending_cue": null,
  "cue_priority": null,
  "pulse_in_flight": false,
  "narrator_muted": false,
  "tick_count": 3,
  "started_at": "…",
  "last_tick_at": "…",
  "vars": { "current_camera": "cam1" },
  "camera_labels": { "cam1": "Wide" },
  "senses": [
    { "key": "phase", "label": "PHASE", "value": "running" },
    { "key": "pulse_mode", "label": "MODE", "value": "directed" },
    { "key": "current_camera", "label": "CAMERA", "value": "Wide" },
    { "key": "pending_cue", "label": "CUE", "value": "—" }
  ],
  "ts": "…"
}
```

`senses` is projected from `session_config.panel.senses` (or a default of `phase` / `pulse_mode` / `pending_cue`). Full `session_config` is not broadcast.

## On connect

The server immediately sends the latest cached `persona`, `turn_state`, and `pulse_state` snapshots (if any), then streams live events.

# Buddy companion panel

Local sci-fi HUD for voice sessions: turn-state orb, live captions, and pulse senses readout. Connects to the voice process over a localhost WebSocket bridge (`ws://127.0.0.1:8766` by default).

## Run with voice (recommended)

From the repo root:

```powershell
.\start-buddy.ps1
```

That opens llama-server, the voice agent, and this panel in separate windows. If llama is already up:

```powershell
.\start-speech-to-speech.ps1
```

Both launchers start the panel non-blocking and enable the companion bridge when it is not already set in `.env`.

## Run standalone

Useful for UI work or mock/demo mode when the bridge is down:

```powershell
cd companion
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. Without the voice bridge, the panel shows connection status and falls back to mock telemetry.

Production-style static serve (after building):

```powershell
npm run build
npm run preview
```

## Bridge configuration

The voice process publishes events when `BUDDY_COMPANION_BRIDGE=1` (set automatically by the voice launchers unless you override it). Optional overrides in repo-root `.env`:

```env
BUDDY_COMPANION_BRIDGE=1
BUDDY_COMPANION_BRIDGE_HOST=127.0.0.1
BUDDY_COMPANION_BRIDGE_PORT=8766
```

Event schema: `buddy_tools/companion/SCHEMA.md`.

## OBS Browser Source widgets

The companion app serves transparent stream widgets for OBS Studio. With Buddy voice running (so this panel and the bridge are up), add **Browser Source** entries for the pieces you want — each URL is independently placeable:

| Widget | Stream (production) | Setup / wiring |
|--------|---------------------|----------------|
| Speaking orb | `http://127.0.0.1:5173/obs/speaking` | `…/obs/speaking?debug=1` |
| Captions | `http://127.0.0.1:5173/obs/captions` | `…/obs/captions?debug=1` |

Recommended OBS settings (both):

- **Width / height:** speaking e.g. `400` × `400`; captions e.g. `1280` × `100` (a short strip — scale in the preview as needed)
- **Shutdown source when not visible:** off (so the widget can appear instantly)
- **Control audio via OBS:** off
- Background is transparent — no chroma key required

Behavior:

- **Speaking:** orb only while the bridge reports `turnState === "speaking"`, then a short fade-out
- **Captions:** single-line karaoke strip — blank until speaking starts; active word stays slightly left-of-center with soft edge fades; fades out with the speaking orb (~450ms), then clears. Idle / disconnected stays fully transparent
- Production URLs never mock; `?debug=1` shows link status and runs mock turn/caption cycles for positioning

Operator HUD remains at `http://127.0.0.1:5173/` (unchanged).

## Ports

| Service | Default |
|---------|---------|
| Vite dev / preview | `127.0.0.1:5173` |
| Companion bridge WS | `127.0.0.1:8766` |
| OBS speaking widget | `http://127.0.0.1:5173/obs/speaking` |
| OBS captions widget | `http://127.0.0.1:5173/obs/captions` |

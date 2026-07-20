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

## Ports

| Service | Default |
|---------|---------|
| Vite dev / preview | `127.0.0.1:5173` |
| Companion bridge WS | `127.0.0.1:8766` |

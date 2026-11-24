# Live Call Link Demo

During a (simulated) phone call, an AI agent texts the customer a temporary, secured link that
shows live what the agent heard, what it's noting and doing, and the resulting cart or
reservation. The customer confirms there before anything is committed. CLAUDE.md is the full
brief. Telephony is simulated; the agent, tools, database and link are live.

## Run

```bash
make install      # first time: uv sync + npm install
make db-up        # Postgres 16 on localhost:5433
make seed         # wipe and load demo data (needed after schema changes)
make backend      # terminal 1: FastAPI on 127.0.0.1:8000 (loopback only)
make frontend     # terminal 2: Vite on 0.0.0.0:5180 (FRONTEND_PORT); proxies /api and /ws to :8000
```

| What | Where |
|---|---|
| Customer link page | `/s/<token>` |
| Live call link | `make live S=order MODE=manual SPEED=1` prints it |
| Screenshots (dev) | `/dev/screenshots` (files in `frontend/public/screenshots/`, gitignored) |
| Raw call events (dev) | `/dev/events/<call_id>` |

Other targets: `make test` (backend tests, no LLM calls), `make call S=order SPEED=4` (one scripted call
in-process with an end-state check; add `TTS=1` for real voices), `make screenshots` (link page, headless),
`make director-screenshots` (director replays of the starred takes, headless).

## Recording (director)

Open `http://<tailscale-ip>:5180/director` in Chrome at 1440x900.

- **Live**: a real call with the live agent and OpenAI voices. Press Start; the 3-2-1 countdown gives you
  time to start recording (the click also unlocks audio). Confirm is a real click inside the link page.
- **Replay**: re-runs a recorded passing call as a new call with a fresh real link token, playing the
  recorded turns and voices with silent gaps capped at 700 ms. It pauses where the customer tapped
  Confirm until you click Confirm in the link page. Pick a take; ★ copies it into `backend/takes/`.

Takes come from live runs with voices: `make call S=order TTS=1` (writes `backend/runs/*.json`). Audio is
cached in `backend/audio_cache/` (gitignored) and regenerated from a take's manifest if missing.

## Remote access (Mac -> Tailscale -> this PC)

Vite listens on `0.0.0.0:5180` inside WSL (NAT mode). To reach it from the tailnet:

1. In WSL: `make remote-info` prints the networking mode, this PC's Tailscale IP and the exact command.
2. On Windows, in an **elevated** PowerShell:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu-22.04\home\usr\SimpleAI_Demo\scripts\windows\expose-demo.ps1" -Distro Ubuntu-22.04
   ```
   NAT mode: a `netsh portproxy` from `<tailscale-ip>:5180` to the WSL IP, plus an inbound firewall rule
   "Call Link Demo 5180 (Tailscale only)" for TCP 5180 from `100.64.0.0/10`. Mirrored mode: firewall
   rules only. Idempotent. **Re-run it after a WSL restart or reboot** (the WSL IP changes).
3. On the Mac: `http://<tailscale-ip>:5180/dev/screenshots`, and the links that `make live` prints.
   For those links, set `PUBLIC_BASE_URL=http://<tailscale-ip>:5180` in `backend/.env` (gitignored;
   the default is `http://localhost:5180`).
4. Undo with `unexpose-demo.ps1` (same folder). It removes only what expose added.

Rules for this machine: never run or change `tailscale serve`, `funnel`, `cert` or any Tailscale config
(another project uses this machine's ts.net name); never `wsl --shutdown` or edit `.wslconfig`;
headless browsers only.

Anyone on the tailnet can reach the dev endpoints through the Vite proxy (`/api/dev/*`, including
`/api/dev/calls/{id}/link`). Set `ENABLE_DEV_ROUTES=false` in `backend/.env` when that matters.

[200~# Live Call Link Demo — project brief

## What this is
A demo for a voice-AI sales company: during a phone call, the AI agent texts the customer a
**temporary, secured link**. The page shows, live, what the agent heard, what it's noting, what it's
doing, and the resulting cart or reservation, so the customer can catch mistakes and confirm
before anything is committed.

The deliverable is two 60–90s screen recordings (one per scenario). Optimize for what is visible on
screen. Do not build anything the recording won't show.

## Honesty rule (important)
- Telephony is **simulated**: no Twilio or PSTN. The call is an in-process session with TTS audio.
- Everything after "connected" is **real**: the agent is a live LLM making tool calls against
  Postgres, the link token is real, and the link page renders from database state over a websocket,
    never from the script.
    - The director view shows a small footer: "Telephony simulated · agent, tools, database and link are live".

    ## Roles (use these names everywhere, not caller/callee)
    - **agent**: the business's AI (LLM + tools + DB access). Always the business side.
    - **customer**: the human side, played by a **scripted** beat file. Has no DB access.
    - Scenario A (order) is **outbound**: agent calls customer.
    - Scenario B (reservation) is **inbound**: customer calls the restaurant and the agent answers.
    - In both, the agent offers the link. The token is **minted at `connected`** (not at dial) and only
      **sent after the customer says yes**.

      ## Stack
      - Frontend: React + TypeScript (Vite), Tailwind. `/frontend`
      - Backend: Python 3.11+, FastAPI, websockets, SQLAlchemy 2.x (async) + asyncpg. `/backend`
      - DB: PostgreSQL 16 in docker-compose (host port **5433** to avoid clashing with a local Postgres).
      - LLM: Anthropic Messages API with tool use. Model from env `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`).
      - TTS: OpenAI TTS. Model and voices from env (check current OpenAI docs for the model name).
        Two distinct voices: one for agent, one for customer.
        - No Rails, no AWS, no Alembic (use `create_all` + a seed script; this is a one-day demo).
        - Dev env: Windows + WSL2 (NAT networking mode). Repo lives in the Linux filesystem (`~/...`), not `/mnt/c`.
          The user works remotely (Mac -> Tailscale -> SSH -> WSL/tmux); see "Remote access" below.

        ## Secrets
        - Keys live ONLY in `backend/.env` (gitignored), loaded with python-dotenv. Ship `.env.example`.
        - **Never** `export ANTHROPIC_API_KEY` in the shell or in `~/.bashrc`: Claude Code would then bill
          the API key instead of the user's subscription.
          - Env vars: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `OPENAI_API_KEY`, `OPENAI_TTS_MODEL`,
            `TTS_VOICE_AGENT`, `TTS_VOICE_CUSTOMER`, `DATABASE_URL`, `PUBLIC_BASE_URL`, `LINK_TTL_MINUTES` (default 30).

            ## Core architecture
            - **call_events is the source of truth for the UI.** Append-only table + in-process pub/sub keyed by
              `call_id`. Every state change writes an event, then broadcasts it. The link page and director view
                subscribe over websocket and can rebuild from history on (re)connect. Replay mode re-emits a saved
                  run's events with original timing.
                  - Event types (string enum):
                    `call.status` (dialing|ringing|connected|ended), `link.minted`, `link.sent`, `link.opened`,
                      `link.revoked`, `transcript.turn` (speaker, text_said, text_heard), `transcript.correction`
                        (turn_id, corrected_text), `note.added`, `agent.tool_call` (name, args, result summary),
                          `state.cart`, `state.reservation`, `confirm.requested`, `confirm.received`, `audio.play`.
                          - **Link tokens**: 32-byte urlsafe random. Store only the sha256 hash, plus call_id, expires_at,
                            revoked_at, opened_at. Revoke on `call.status=ended`. Both the page API and the websocket validate
                              the token. An expired or revoked token shows a clean "This link has expired" page.
                              - **Mishear mechanic**: a customer beat may have `heard_as`. TTS speaks `say` (the truth); the
                                transcript records `text_heard` and the agent LLM receives `heard_as`. A later correction emits
                                  `transcript.correction`, and the UI strikes through the wrong words.
                                  - **Confirmation is a round-trip**: the agent calls `request_confirmation` → the page shows the Confirm
                                    screen → a real click POSTs `confirm.received` → the agent hears it, acknowledges aloud, and only then
                                      commits (order status `placed` / reservation status `confirmed`).

                                      ## Agent tools
                                      `lookup_product`, `add_to_cart`, `update_cart_item`, `remove_cart_item`, `get_reservation`,
                                      `check_availability(date)`, `update_reservation`, `add_note`, `send_link`, `request_confirmation`,
                                      `end_call`. Tools hit the DB and emit events. Tool results go back to the LLM.

                                      ## Screens
                                      - **Link page `/s/:token`** (what the customer sees). Desktop: two panes.
                                        - Right: chat-style transcript, labeled "What the agent heard". Corrections shown with strikethrough.
                                          - Left tabs: **Live view** (cart with product images, or a month calendar with the booked date and
                                              open slots) · **Notes** · **Agent actions** (tool-call log in plain English) · **Confirm**
                                                  (appears when requested).
                                                    - Header: brand, "Live · expires in mm:ss", call status dot.
                                                      - Below 768px it collapses to a single column with tabs (Transcript becomes a tab).
                                                      - **Director view `/director`** (what gets recorded): scenario picker, Start button, call-status
                                                        timeline (dialing → ringing → connected → link minted → link sent → opened → … → ended → revoked),
                                                          audio playback, and the link page embedded in a phone-sized frame or opened side-by-side. Honesty footer.

                                                          ## Scenarios (fictional brands only; never use real company names)
                                                          Dates: "today" in the demo is Tue 2026-11-24. Sat 2026-12-05 → Fri 2026-12-04.

                                                          ### A. Order — outbound — "Fieldstone Provisions" (premium meat delivery)
                                                          Customer: Jamie Park. Showed interest in the Ribeye (lead record). Cart starts empty.
                                                          1. Ringing → connected (token minted). Agent: "Hi Jamie, this is Ava from Fieldstone Provisions. I
                                                             wanted to catch up with you about your interest in our dry-aged ribeyes."
                                                             2. Short premise exchange.
                                                             3. Agent offers the link ("I can text you a link that shows your cart and what I'm noting as we talk. Want it?") → yes → `send_link`.
                                                             4. Customer: "I'd like two of the ribeyes" — **heard_as "ten"** → cart shows 10 × ribeye.
                                                             5. Customer sees the cart: "Oh no, two, not ten" → `update_cart_item` → correction struck through.
                                                             6. Agent upsells one add-on (e.g. garlic-herb compound butter) → it appears in the cart with an image → customer accepts.
                                                             7. Agent: "Could you take a look at the final page on the link and confirm before I place it?" → `request_confirmation`.
                                                             8. Real click → agent acknowledges → order `placed` → `end_call` → link revoked → expired page.

                                                             ### B. Reservation change — inbound — "Juniper Table" (restaurant)
                                                             Customer: Daniel Cho. Existing reservation: party of 4, **Sat 2026-12-05 19:00**. Availability: Fri
                                                             2026-12-04 19:00 is full; 18:30 and 19:30 are open.
                                                             1. Customer dials → connected (token minted). Agent answers and greets.
                                                             2. Customer: needs to change the reservation, one more person coming. Agent looks it up by caller phone number → updates party 4 → 5.
                                                             3. Agent offers the link → yes → `send_link`. Calendar shows Sat Dec 5 highlighted.
                                                             4. Customer, looking at calendar: "Oh, I didn't realize the 5th is a Saturday. Can we move it a day earlier, to Friday?"
                                                             5. Agent `check_availability` → 19:00 full, offers 18:30 / 19:30 (open slots visible on calendar) → customer picks 19:30.
                                                             6. Agent asks for confirmation on the final page → real click → acknowledges → `confirmed` → end → revoked.

                                                             ## Cut line
                                                             If the LLM agent is unreliable by ~17:00 KST, switch the agent's spoken lines to scripted text while
                                                             still firing real tool calls at each beat. The page stays DB-driven.

                                                             ## Conventions
                                                             - Keep it simple and demo-grade, but no fake state in the UI: everything rendered comes from events or the DB.
                                                             - Product images: clean, generated SVG illustrations in `frontend/public/products/` (no downloaded photos).
                                                             - Run commands: `docker compose up -d`, `backend: uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`,
                                                               `frontend: npm run dev` (port **5180** via `FRONTEND_PORT`, host 0.0.0.0, strictPort; proxies `/api` and `/ws`
                                                               to 127.0.0.1:8000). Make targets: `make backend`, `make frontend`, `make live`, `make remote-info`.
                                                             - Screenshots go to `frontend/public/screenshots/` (gitignored), browsable at `/dev/screenshots`.
                                                             - TTS clips are cached in `backend/audio_cache/` (gitignored), served at `/api/audio/{hash}.mp3`.
                                                               Every live call writes `backend/runs/<scenario>-<ts>.json` (events + checks + audio manifest).
                                                               Starred takes live in `backend/takes/` (committed). Replay: `POST /api/replay {run, max_gap_ms}`.

## Remote access (Tailscale)
- The user opens the frontend from a Mac at `http://<tailscale-ip>:5180` (`make remote-info` prints this
  PC's Tailscale IP and MagicDNS name). Only the Vite port is exposed; uvicorn stays on 127.0.0.1:8000.
- `PUBLIC_BASE_URL` defaults to `http://localhost:5180`. For the Mac, set it to `http://<tailscale-ip>:5180`
  in `backend/.env` (gitignored), so links from `/api/dev/calls/{id}/link` open there.
  Never commit IP addresses, hostnames or keys: machine-specific values live only in `backend/.env`.
- Exposing is done by the user in an elevated Windows PowerShell with `scripts/windows/expose-demo.ps1`
  (NAT: netsh portproxy tailscale-ip:5180 -> wsl-ip:5180, plus a firewall rule for 100.64.0.0/10 only).
  Undo with `unexpose-demo.ps1`. Re-run expose after a WSL restart (the WSL IP changes). `make remote-info` prints the commands.
- Hard rules:
  - Never run or modify `tailscale serve`, `tailscale funnel`, `tailscale cert`, or any Tailscale config.
    Another project uses this machine's ts.net domain. Read-only `tailscale.exe ip -4` / `status --json` is fine.
  - Never run `wsl --shutdown` or change `.wslconfig`: it kills the session Claude Code runs in.
  - Headless browsers only. Never launch a headed browser or open anything on Windows
    (no explorer.exe, wslview, `cmd.exe /c start`).
  - Don't try to elevate on Windows; the user runs the PowerShell scripts.
[201~]]

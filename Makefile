.PHONY: install db-up db-down db-reset seed backend frontend test call screenshots director-screenshots live remote-info

install:            ## Install backend (uv) and frontend (npm) dependencies
	cd backend && uv sync
	cd frontend && npm install

db-up:              ## Start Postgres 16 on localhost:5433 and wait until healthy
	docker compose up -d --wait db

db-down:            ## Stop Postgres (data volume is kept)
	docker compose down

db-reset:           ## Stop Postgres and delete its data volume
	docker compose down -v

seed:               ## Drop all tables, recreate, and load demo data
	cd backend && uv run python -m app.seed

backend:            ## FastAPI on http://127.0.0.1:8000 (auto-reload; loopback only, never exposed)
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:           ## Vite on 0.0.0.0:5180 (FRONTEND_PORT); proxies /api and /ws to 127.0.0.1:8000
	cd frontend && npm run dev

test:               ## Backend tests (separate simpleai_test DB; no LLM calls)
	cd backend && uv run pytest -q

call:               ## Scripted call with the live agent: make call S=order SPEED=4 [MANUAL=1] [TTS=1]
	cd backend && uv run python -m app.call.run $(or $(S),order) --speed $(or $(SPEED),1) $(if $(MANUAL),--manual) $(if $(TTS),--tts)

screenshots:        ## Link page screenshots for both scenarios (needs `make backend` + `make frontend`)
	cd frontend && npm run screenshots

live:               ## Start a call on the running backend, print its link: make live S=order MODE=manual SPEED=1
	scripts/live-call.sh $(or $(S),order) $(or $(MODE),manual) $(or $(SPEED),1)

remote-info:        ## Networking mode, Tailscale URLs, and the PowerShell command to expose port 5180
	scripts/remote-info.sh

director-screenshots: ## Replay each starred take in the director (headless, auto-confirm) and screenshot it
	cd frontend && node scripts/director-screenshots.mjs

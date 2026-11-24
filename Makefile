.PHONY: install db-up db-down db-reset seed backend frontend

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

backend:            ## FastAPI on http://127.0.0.1:8000 (auto-reload)
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:           ## Vite on http://localhost:5173 (proxies /api and /ws to :8000)
	cd frontend && npm run dev

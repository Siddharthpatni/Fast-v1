.PHONY: help up down build e2e logs backend-logs frontend-logs db-shell redis-cli migrate test lint format init dev-data real-data pull-model eval train-capacity train-wait db-reset

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Docker ──
up: ## Start all services
	docker compose up -d

down: ## Stop all services
	docker compose down

build: ## Rebuild all containers
	docker compose build --no-cache

logs: ## Tail all logs
	docker compose logs -f

backend-logs: ## Tail backend logs
	docker compose logs -f backend

frontend-logs: ## Tail frontend logs
	docker compose logs -f frontend

# ── Database ──
db-shell: ## Open psql shell
	docker compose exec db psql -U fastmile -d fastmile

redis-cli: ## Open Redis CLI
	docker compose exec redis redis-cli

migrate: ## Run Alembic migrations
	docker compose exec backend alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create MSG="add stops table")
	docker compose exec backend alembic revision --autogenerate -m "$(MSG)"

# ── Development ──
test: ## Run backend tests (in the backend container)
	docker compose exec backend python -m pytest -q  # -m puts /app on sys.path

e2e: ## End-to-end UI tests in headless Chromium (Docker); screenshots in e2e/artifacts/
	docker compose --profile e2e run --rm e2e

lint: ## Lint backend code
	docker compose exec backend ruff check app/

format: ## Format backend code
	docker compose exec backend ruff format app/

# ── Setup ──
init: ## First-time setup: copy env, build, start, migrate
	@test -f .env || cp .env.example .env
	docker compose build
	docker compose up -d
	@echo "Waiting for DB..."
	@sleep 5
	docker compose exec backend alembic upgrade head
	@echo "✅ FastMile is running at http://localhost:5173"

# ── Data & models ──
dev-data: ## Regenerate the dev fixtures in data/ (GTFS, GBFS, on-demand zones)
	python3 backend/scripts/make_dev_data.py

real-data: ## Download the free gtfs.de feeds and cut them to Lower Saxony and around (Harz, Hannover, Bremen, Göttingen, Magdeburg)
	mkdir -p data/raw
	curl -L -o data/raw/gtfs_nv.zip https://download.gtfs.de/germany/nv_free/latest.zip
	curl -L -o data/raw/gtfs_rv.zip https://download.gtfs.de/germany/rv_free/latest.zip
	curl -L -o data/raw/gtfs_fv.zip https://download.gtfs.de/germany/fv_free/latest.zip
	docker compose exec backend python scripts/build_region_feed.py --bbox 51.30,8.80,53.20,11.70 --out /data/gtfs_region /data/raw/gtfs_nv.zip /data/raw/gtfs_rv.zip /data/raw/gtfs_fv.zip
	docker compose restart backend

pull-model: ## Download the local LLM for disruption extraction (needs Ollama running)
	ollama pull qwen2.5:7b-instruct

eval: ## Score disruption extraction (LLM vs rules, plus Jev severity) on the labelled samples
	docker compose exec backend python -m app.services.llm.evaluate

train-capacity: ## Train the occupancy model (usage: make train-capacity CSV=/data/raw/occupancy.csv)
	docker compose exec backend python -m app.services.ml.train capacity --csv $(CSV)

train-wait: ## Train the on-demand wait model (usage: make train-wait CSV=/data/raw/trips.csv)
	docker compose exec backend python -m app.services.ml.train wait --csv $(CSV)

db-reset: ## Drop the database volume and start fresh (all stored disruptions are lost)
	docker compose down -v
	docker compose up -d

.PHONY: help up down logs migrate seed seed-demo test test-be test-fe lint fmt build clean shell psql redis worker prod-up prod-down prod-logs prod-migrate prod-pull prod-logs-ops backup restore generate-sitemap free-up free-down free-logs free-migrate bootstrap

help:
	@echo "up          docker compose up (build)"
	@echo "down        stop and remove containers"
	@echo "logs        tail api + worker logs"
	@echo "migrate     alembic upgrade head"
	@echo "seed        seed cities/dishes/aliases/badges"
	@echo "seed-demo   seed synthetic reviews for a populated UI"
	@echo "test        backend + frontend tests"
	@echo "lint        ruff + tsc + eslint"
	@echo "build       production frontend build"

up:
	docker compose up --build -d
	@echo "api  -> http://localhost:8000/docs"
	@echo "web  -> http://localhost:5173"

down:
	docker compose down

logs:
	docker compose logs -f api worker

migrate:
	docker compose exec api alembic upgrade head

seed:
	docker compose exec api python -m scripts.seed --city kolkata

seed-demo:
	docker compose exec api python -m scripts.seed_demo

test: test-be test-fe

test-be:
	cd backend && pytest -q

test-fe:
	cd frontend && npm run typecheck && npm run test -- --run

lint:
	cd backend && ruff check app scripts tests
	cd frontend && npm run lint

fmt:
	cd backend && ruff format app scripts tests
	cd frontend && npx prettier --write "src/**/*.{ts,tsx,css}"

build:
	cd frontend && npm run build

shell:
	docker compose exec api bash

psql:
	docker compose exec db psql -U khaabo -d khaabo

worker:
	docker compose exec worker celery -A app.workers.celery_app inspect active

clean:
	docker compose down -v

# ── production (compose overlay) ────────────────────────────────────────────
PROD_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml

prod-up:
	$(PROD_COMPOSE) up -d
	@echo "api  -> https://$$(grep DOMAIN .env 2>/dev/null | cut -d= -f2 || echo 'localhost')  (via Caddy)"
	@echo "docs -> https://$(DOMAIN)/docs"

prod-down:
	$(PROD_COMPOSE) down

prod-logs:
	$(PROD_COMPOSE) logs -f api worker

prod-migrate:
	$(PROD_COMPOSE) run --rm --no-deps api alembic upgrade head

prod-pull:
	$(PROD_COMPOSE) pull

prod-logs-ops: ## monitoring stack (Prometheus + Loki + Grafana)
	$(PROD_COMPOSE) --profile ops up -d prometheus loki promtail grafana
	@echo "grafana -> http://localhost:3000  (admin / \$$GRAFANA_ADMIN_PASSWORD)"
	@echo "prom    -> http://localhost:9090"

backup:
	DATABASE_URL="postgresql+psycopg://$$(grep DB_USER .env | cut -d= -f2):$$(grep DB_PASSWORD .env | cut -d= -f2)@$$(grep DB_HOST .env 2>/dev/null | cut -d= -f2 || echo db):5432/$$(grep DB_NAME .env 2>/dev/null | cut -d= -f2 || echo khaabo)" \
		./scripts/backup-db.sh

restore:
	@echo "Usage: make restore DUMP=/path/to/khaabo-*.dump [FORCE=--force]"
	./scripts/restore-db.sh $(FORCE) $(DUMP)

generate-sitemap:
	docker compose exec api python -m scripts.generate_sitemap --output /app/../frontend/public/sitemap.xml --base-url $$(grep PUBLIC_BASE_URL .env | cut -d= -f2)

# ── free-tier (external DB + Redis, only api/worker/beat/caddy on the VM) ───
FREE_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.free.yml

free-up:
	$(FREE_COMPOSE) up -d
	@echo "api  -> https://$$(grep DOMAIN .env | cut -d= -f2)"
	@echo "docs -> https://$$(grep DOMAIN .env | cut -d= -f2)/docs"

free-down:
	$(FREE_COMPOSE) down

free-logs:
	$(FREE_COMPOSE) logs -f api worker beat caddy

free-migrate:
	$(FREE_COMPOSE) run --rm --no-deps api alembic upgrade head

bootstrap: ## provision a fresh Oracle Cloud VM (run with sudo on the VM)
	sudo bash scripts/prod-bootstrap.sh

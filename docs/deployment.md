# Khaabo — Deployment

Single `docker-compose.yml`, no Kubernetes, no Kafka, no service mesh. Everything can run
on one 2 vCPU / 4 GB VM, or on free tiers.

---

## 1. Development

```bash
cp .env.example .env          # fill Supabase keys; everything else has working defaults
docker compose up --build     # postgis, redis, api, worker, beat, web
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed --city kolkata
```

| Service | Port | Notes |
|---|---|---|
| `web` | 5173 | Vite dev server, proxies `/api` → api |
| `api` | 8000 | FastAPI, `--reload`, docs at `/docs` |
| `worker` | – | Celery, queues `ingestion,ai_processing,ranking,summarization` |
| `beat` | – | Celery beat scheduler |
| `db` | 5432 | `postgis/postgis:16-3.4` |
| `redis` | 6379 | `redis:7-alpine` |
| `ollama` | 11434 | optional profile `ai` |

Ollama is behind a compose profile: `docker compose --profile ai up -d ollama` then
`docker compose exec ollama ollama pull llama3.1:8b`. Without it, the heuristic AI
provider is used and the product still functions end to end.

## 2. Environment

`.env.example` documents every variable. Required in production:

```text
DATABASE_URL, REDIS_URL, SECRET_KEY
SUPABASE_URL, SUPABASE_JWT_SECRET (or SUPABASE_JWKS_URL), SUPABASE_ANON_KEY
CORS_ORIGINS, PUBLIC_BASE_URL
CONTACT_EMAIL          # required by the Nominatim usage policy
```

Optional: `REDDIT_CLIENT_ID/SECRET/USER_AGENT`, `YOUTUBE_API_KEY`, `AI_PROVIDER`,
`OLLAMA_BASE_URL`, all `RANKING_W_*` and `TREND_*` tuning knobs.

Frontend gets only `VITE_API_BASE_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.
The anon key is public by design; the **service key and JWT secret never reach the
frontend** and `.env` is gitignored.

## 3. Production topology (free-tier friendly)

```text
Cloudflare (DNS, TLS, cache) — optional, in front of Caddy
      │
      └── api + static  →  Caddy :443 (auto-TLS, security headers)
                              ├── /api/*  → uvicorn :8000 (2 workers)
                              └── /*      → nginx :80 (static SPA)
                              ↑
      Celery worker + beat  →  Redis (broker/cache)
                              →  Postgres+PostGIS
```

Options that keep cost at zero: Supabase free Postgres (PostGIS available) + Fly.io /
Oracle Cloud Always Free / Hetzner CX11 for api+worker, Cloudflare Pages for the SPA,
Upstash free Redis. Nothing in the code assumes a specific host.

### 3.1 Production compose

The production overlay is in `docker-compose.prod.yml` and uses Caddy as the
edge reverse proxy:

```bash
# Secrets + domain must be in .env (see .env.example for the full list)
ENV=production SECRET_KEY=<32+ chars> DOMAIN=khaabo.in \
  CORS_ORIGINS=https://khaabo.in PUBLIC_BASE_URL=https://khaabo.in \
  CONTACT_EMAIL=ops@khaabo.in DB_PASSWORD=<strong> \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Or via Make:

```bash
make prod-up      # starts the full prod stack
make prod-logs    # tail api + worker
make prod-pull    # pull latest images
make prod-migrate # run migrations as a one-shot
make prod-down    # stop everything
```

Differences from the dev compose:

- `target: prod` images (non-root, no dev deps, no `--reload`)
- No source bind mounts — the image is the source of truth
- Resource limits per service (db 1G, api 512M, worker 1G, beat 256M, web 64M)
- Log rotation capped at 3 × 10MB per service
- Caddy on `:80/:443` with auto-TLS — no port exposure for api/web
- `ENV=production` validators in the app refuse to start without proper secrets

### 3.2 Monitoring (optional)

The `ops` profile adds Prometheus (metrics scrape), Loki + Promtail (log
aggregation), and Grafana (dashboard):

```bash
make prod-logs-ops
```

Dashboards are provisioned from `deploy/grafana/dashboards/` automatically.
The API exposes `GET /api/v1/metrics` (Prometheus format) with request rate,
error rate, and latency histograms. Sentry (if `SENTRY_DSN` is set) captures
exceptions with stack traces in both the API and Celery workers.

## 4. Migrations & data

```bash
alembic upgrade head                  # schema
python -m scripts.seed --city kolkata # cities, dishes, dish aliases, badges
python -m scripts.seed_demo           # optional synthetic reviews for a populated UI
```

Migrations run as a one-shot job before rolling the API. Never autogenerate against
production without review; enums and PostGIS indexes need explicit ops.

## 5. Operations

- Backups: nightly `pg_dump` to object storage, 7-day retention; restore is documented and
  must be tested before launch.
- Logs: structured JSON to stdout (`request_id`, `user_id`, `route`, `latency_ms`); the
  host collector ships them. Sentry is optional via `SENTRY_DSN`.
- `/health` reports db, redis, and AI provider reachability separately so a degraded AI
  path does not fail the container healthcheck.
- Celery is monitored via `celery -A app.workers.celery_app inspect active` plus the admin
  failed-jobs view; Flower is available on the `ops` profile.

## 6. Launch checklist

1. `CORS_ORIGINS` set to the real domain, no wildcard.
2. `DEBUG=false`, `SECRET_KEY` rotated, default admin removed.
3. Supabase JWT verification confirmed against a real token (`aud`, `exp`, signature).
4. Rate limits verified with a load test on `POST /reviews`.
5. `CONTACT_EMAIL` + descriptive `User-Agent` set for Nominatim/Overpass compliance.
6. OSM attribution visible on every map and on OSM-derived responses.
7. Ingestion intervals ≥ 6 h; YouTube quota guard confirmed under the daily cap.
8. Backups verified by an actual restore into a scratch DB.
9. `robots.txt` + `sitemap.xml` generated for dish/restaurant slugs.
10. Moderation queue staffed before user reviews are enabled.

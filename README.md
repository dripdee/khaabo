# Khaabo

**Dish-first food discovery. Launching in Kolkata.**

> The product answers *"What should I eat, and where should I eat it?"* — not
> *"Which restaurant has the highest rating?"*

Search a dish → see the places ranked **for that dish** → read the evidence behind each
position → compare on a map → save it → review it → improve the rankings.

Built entirely on free and open data: OpenStreetMap/Overpass, the official Reddit and
YouTube APIs, and user submissions. No Google Places, no paid LLM API, no scraping.

---

## Why it is different

| Most apps | Khaabo |
|---|---|
| One rating per restaurant | A separate score per **dish** at each restaurant |
| "4.3 ★" with no reasoning | A short **"Why?"** built from stored evidence |
| Guesses a rank from 2 reviews | Says **"Not enough data"** and means it |
| A model writes the summary | Summaries are composed from stored evidence only |

A place can make outstanding momo and forgettable biryani. Averaging that into a single
number throws away the only thing you actually wanted to know.

---

## Quick start

```bash
git clone <repo> khaabo && cd khaabo
cp .env.example .env          # works as-is for local dev
docker compose up --build -d
docker compose exec api python -m scripts.seed --city kolkata
docker compose exec api python -m scripts.seed_demo   # optional: a populated UI
```

| Service | URL |
|---|---|
| Web | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/api/v1/health |

Then search **“best chicken momo”**.

Nothing above requires an API key. Supabase, Reddit, YouTube and a local LLM are all
optional — the product runs and ranks without any of them.

### Without Docker

```bash
# backend
cd backend
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload

# worker (separate shell)
celery -A app.workers.celery_app worker -Q ingestion,ai_processing,ranking,summarization

# frontend
cd frontend && npm install && npm run dev
```

Postgres **with PostGIS** and Redis must be reachable. `docker compose up -d db redis`
gives you both without running the app containers.

---

## Stack

**Frontend** React 18 · Vite · TypeScript · Tailwind · Framer Motion · TanStack Query ·
React Router · React Hook Form + Zod · Leaflet + OpenStreetMap

**Backend** FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 + PostGIS 3.4 · Alembic ·
Celery + Redis · Supabase Auth (JWT verification only)

**AI** Heuristic provider by default (zero dependencies), Ollama or any
OpenAI-compatible endpoint optionally. Providers sit behind one interface.

---

## How ranking works

```text
Score = 35% sentiment + 20% recency + 15% consistency + 10% volume
      + 10% source quality + 5% engagement + 5% confidence
```

All weights are env-configurable, validated to sum to 1.0 at startup, and each stored
score records its `weights_version`.

Three properties matter more than the weights themselves:

1. **Bayesian shrinkage** pulls thin evidence toward a dish-and-city prior, so three
   glowing mentions cannot outrank five hundred consistently good ones. Asserted by
   `test_two_reviews_cannot_beat_five_hundred`.
2. **Time decay** (180-day half-life) makes recent evidence matter more without
   erasing older evidence.
3. **An honesty gate** — under 3 mentions, or too little evidence weight, and the row
   is stored as `insufficient_data` with a `NULL` score and excluded from rankings.

Trends compare the last 60 days against the preceding six months and are emitted
**only** when both windows clear the observation threshold. No arrow is shown otherwise.

Full detail: [`docs/ranking.md`](docs/ranking.md).

---

## The "Why?" line

Explanations are never model-written. `dish_scores.why` stores structured reason codes:

```json
[
  {"code": "positive_ratio", "label": "91% positive dish sentiment", "value": 0.91},
  {"code": "recent",         "label": "strong recent reviews",       "value": 0.84},
  {"code": "consistency",    "label": "consistent quality",          "value": 0.79},
  {"code": "mentions",       "label": "42 dish mentions",            "value": 42}
]
```

The frontend joins these labels and composes nothing of its own, so the sentence is a
rendering of the score rather than a claim about the world. It can also be unflattering:
`mixed reports` and `no recent mentions` are valid reason codes.

---

## Multi-dish extraction

> "Chicken momo is amazing but biryani is average."

produces **two** `review_dish_mentions` rows with independent sentiment, aspects and
prices. Clause splitting on contrast markers is deterministic Python, so this works with
no model installed (`test_canonical_multi_dish_case`).

---

## Documentation

| Doc | Contents |
|---|---|
| [architecture.md](docs/architecture.md) | System shape, layering rules, caching, failure posture |
| [database.md](docs/database.md) | ERD, every table, index rationale |
| [api.md](docs/api.md) | Endpoints, search grammar, error envelope |
| [ranking.md](docs/ranking.md) | Weights, shrinkage, badges, trend detection |
| [ai-pipeline.md](docs/ai-pipeline.md) | Stages, JSON contracts, anti-hallucination rules |
| [ingestion.md](docs/ingestion.md) | Source adapters, idempotency, dedup, entity resolution |
| [frontend.md](docs/frontend.md) | Structure, design tokens, component contracts, motion |
| [deployment.md](docs/deployment.md) | Dev and production topology, launch checklist |
| [deploy-free.md](docs/deploy-free.md) | Zero-cost production deploy: Oracle Cloud + Supabase + Upstash + Cloudflare Pages |
| [runbook.md](docs/runbook.md) | Ops runbook: deploys, rollback, backups, incident response |

---

## Project layout

```text
backend/
  app/
    api/v1/        transport only — parse, authorize, delegate
    services/      all business logic; pure cores + DB wrappers
    ai/            provider adapters + grounding enforcement
    ingestion/      source adapters + persistence pipeline
    workers/       thin Celery wrappers
    models/        SQLAlchemy 2.0, PostGIS
  scripts/         seed, seed_demo
  tests/           ranking, trends, dedup, entity resolution, extraction, API
frontend/
  src/
    components/    design system
    features/      search · dishes · restaurants · map · reviews · profiles · bookmarks
    pages/         routes
    services/      typed API client
docs/              design documents
```

---

## Tests

```bash
cd backend  && pytest             # 190+ tests, no services required
cd frontend && npm run typecheck && npm run test -- --run
```

Tests needing PostGIS are marked `db` and **skip automatically** when the database is
not reachable, so a clean checkout is green. In Docker/CI they run for real.

Covered edge cases from the brief: 2 reviews vs 500 · conflicting restaurant names ·
multiple dishes per review · duplicate and near-duplicate reviews · sudden sentiment
swings · insufficient data everywhere it can occur.

---

## Configuration

Everything is env-driven; see [`.env.example`](.env.example). The knobs worth knowing:

| Variable | Purpose |
|---|---|
| `AI_PROVIDER` | `heuristic` (default), `ollama`, `openai_compat` |
| `SOURCES_ENABLED` | `osm,reddit,youtube,user` |
| `SOURCE_INTERVAL_*_HOURS` | Refresh cadence per source (6–24 h) |
| `RANKING_W_*` | Ranking weights |
| `TREND_*` | Trend windows and thresholds |
| `DEFAULT_CITY_SLUG` | Launch city; Kolkata is data, not a code branch |
| `AUTH_DEV_BYPASS` | Dev-only tokens. Startup **fails** if left on in production |

---

## Adding a city

Kolkata is not hard-coded anywhere in business logic.

```bash
python -m scripts.seed --city pune --name Pune --lat 18.5204 --lng 73.8567
```

Ingestion picks it up on the next run. Every query is city-scoped through the `cities`
table, so onboarding is data and configuration only.

---

## Licensing and fair use

- Place data © OpenStreetMap contributors (ODbL), attributed in the UI and stored
  per-record.
- Reddit and YouTube are read through their official APIs, within free quotas, with
  rate limits and backoff respected.
- Nominatim and Overpass are called with an identifying `User-Agent` and contact email,
  at or below their published rate limits.
- No Google Maps/Places data, and no scraping of any review site.

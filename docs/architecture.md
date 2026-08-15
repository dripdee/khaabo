# Khaabo — Architecture

Khaabo is a **dish-first** food discovery product. The unit of discovery is a *dish*
(`chicken momo`, `kosha mangsho`, `tonkotsu ramen`), not a restaurant. Every ranking
carries an evidence-backed **"Why?"** string.

Launch city: Kolkata. The city is *data*, never a branch in business logic.

---

## 1. System shape

```text
                       ┌────────────────────────────────────────────┐
                       │              External sources              │
                       │  Overpass/OSM · Nominatim · Reddit · YT    │
                       └───────────────────┬────────────────────────┘
                                           │  (provider adapters)
                                           ▼
   ┌──────────────┐   enqueue    ┌───────────────────────────────────┐
   │  Celery beat │─────────────▶│  ingestion queue                  │
   └──────────────┘              │  fetch → normalize → dedupe →     │
                                 │  entity resolution → persist raw  │
                                 └──────────────┬────────────────────┘
                                                │ emits review ids
                                                ▼
   ┌──────────────┐              ┌───────────────────────────────────┐
   │ user review  │─────────────▶│  ai_processing queue              │
   │ (API POST)   │  near-RT     │  lang → dishes → aspects →        │
   └──────────────┘              │  sentiment → value → spam         │
                                 └──────────────┬────────────────────┘
                                                │ dirty (dish,restaurant)
                                                ▼
                                 ┌───────────────────────────────────┐
                                 │  ranking queue                    │
                                 │  scores · shrinkage · trends      │
                                 └──────────────┬────────────────────┘
                                                │
                                                ▼
                                 ┌───────────────────────────────────┐
                                 │  summarization queue              │
                                 │  evidence-only dish summaries     │
                                 └──────────────┬────────────────────┘
                                                ▼
                        ┌────────────────────────────────────────────┐
                        │        PostgreSQL 16 + PostGIS 3.4         │
                        └───────────────────┬────────────────────────┘
                                            │
                        ┌───────────────────▼────────────────────────┐
                        │  FastAPI /api/v1   (+ Redis read cache)    │
                        └───────────────────┬────────────────────────┘
                                            │ JSON
                        ┌───────────────────▼────────────────────────┐
                        │  React + Vite + TanStack Query + Leaflet   │
                        └────────────────────────────────────────────┘
```

Auth is **Supabase** (free tier). The backend never issues tokens; it *verifies*
Supabase JWTs and maps `sub` → local `users` row on first sight (JIT provisioning).

---

## 2. Layering rules

| Layer | Location | Rule |
|---|---|---|
| Transport | `app/api/v1/*` | Parse, authorize, delegate. **No business logic.** |
| Schemas | `app/schemas/*` | Pydantic v2 request/response contracts. |
| Domain services | `app/services/*` | All logic. Pure where possible, DB-session-injected. |
| Adapters | `app/ingestion/*`, `app/ai/*` | Replaceable providers behind ABCs. |
| Persistence | `app/models/*`, `app/db/*` | SQLAlchemy 2.0 typed ORM, no raw string SQL concat. |
| Workers | `app/workers/*` | Thin Celery wrappers over domain services. |

A route handler is at most: validate → call one service method → return schema.
This keeps the same logic reusable from Celery tasks and CLI scripts.

### Pure-core principle

`ranking`, `trends`, `dedup`, `dish_extraction` and `entity_resolution` expose
**pure functions over plain dataclasses** (`compute_dish_score(observations, weights)`).
The DB-touching wrapper lives in the same module but is a separate function. This is
what makes the algorithm testable without Postgres, and it is why the test suite can
assert "2 reviews vs 500 reviews" behaviour deterministically.

---

## 3. Data flow contracts

### 3.1 Ingestion is idempotent

Every external item is keyed by `(source, external_id)` with a unique constraint on
`review_sources` / `restaurant_sources`. Re-running a job is a no-op unless the payload
hash changed:

```text
content_hash = sha256(normalized_text | rating | author | created_at)
```

If the hash is unchanged → skip (no AI spend, no ranking churn).
If changed → update row, mark `ai_state='pending'`, re-enqueue.

### 3.2 Entity resolution before anything else

An incoming place/mention resolves to an existing `restaurants` row via, in order:

1. `restaurant_sources` exact `(source, external_id)` hit
2. `restaurant_aliases` normalized-name hit **within the same city**
3. trigram name similarity **AND** geo distance ≤ 250 m
4. otherwise → new restaurant, `confidence` recorded

Ambiguous matches (two candidates within similarity delta) are written to
`entity_conflicts` for the admin queue instead of being guessed.

### 3.3 Dish extraction produces *observations*, not verdicts

One review → N `review_dish_mentions`. The canonical example
`"Chicken momo is amazing but biryani is average"` stores **two** rows with different
`sentiment` values, each linked to its own `review_aspects`.

### 3.4 Ranking is incremental

AI processing emits a *dirty set* of `(dish_id, restaurant_id)` pairs. Only those pairs,
plus their parent dish aggregate, are recomputed. There is a nightly full sweep as a
safety net, not as the primary path.

---

## 4. Caching strategy

| Key | TTL | Invalidated by |
|---|---|---|
| `search:{hash(params)}` | 120 s | time only |
| `dish:{slug}:{city}` | 300 s | ranking job for that dish |
| `dish:{slug}:restaurants:{filters}` | 300 s | ranking job |
| `trending:{city}` | 900 s | trend job |
| `restaurant:{id}:dna` | 600 s | ranking job |

Cache is a Redis read-through wrapper (`app/core/cache.py`) that **degrades to
pass-through** if Redis is unavailable. Nothing in the product hard-requires Redis to
serve a request; Redis is required only for Celery.

---

## 5. Failure posture

- No AI provider configured → `HeuristicProvider` (lexicon + rules) runs. The product
  still works, `confidence` is lower, and `ai_model` records `heuristic-v1`.
- Overpass rate-limited (429) → exponential backoff, job retried, existing data served.
- Insufficient evidence → API returns `"status": "insufficient_data"` and the UI renders
  **"Not enough data"**. It never renders a fabricated rank.
- Trend requires ≥ `TREND_MIN_OBSERVATIONS` in *both* windows, else `trend: null`.

---

## 6. Multi-city

`cities(id, name, slug, country, lat, lng, radius_m, timezone, active)`.

Every query that touches geography takes a `city_id` resolved from either an explicit
`city` slug, or reverse geocode of user coords, or the default from
`DEFAULT_CITY_SLUG`. Onboarding a city = insert a row + run
`seed_city --slug pune` + let ingestion run. No code change.

---

## 7. What is deliberately excluded

Kubernetes, Kafka, microservices, a vector DB, and paid LLM/Places APIs. Everything runs
on one Docker Compose file and can be self-hosted on a single small VM.

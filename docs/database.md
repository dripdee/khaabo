# Khaabo — Database Schema & ERD

PostgreSQL 16 + PostGIS 3.4. All PKs are `uuid` (`gen_random_uuid()` from `pgcrypto`).
All tables carry `created_at`/`updated_at` (`timestamptz`, UTC). Deletes are restricted
or cascaded explicitly — never silent.

Extensions required: `postgis`, `pgcrypto`, `pg_trgm`, `unaccent`.

---

## 1. ERD

```text
                    ┌──────────┐
                    │  cities  │
                    └────┬─────┘
         ┌───────────────┼────────────────┐
         ▼               ▼                ▼
 ┌───────────────┐  ┌─────────┐   ┌───────────────────┐
 │  restaurants  │  │ dishes  │   │ ranking_snapshots │
 └───┬───┬───┬───┘  └──┬───┬──┘   └───────────────────┘
     │   │   │         │   │
     │   │   │  ┌──────▼───▼──────────┐
     │   │   └─▶│  restaurant_dishes  │◀── the dish-first join
     │   │      └──────────┬──────────┘
     │   │                 │
     │   │      ┌──────────▼──────────┐        ┌──────────────┐
     │   │      │     dish_scores     │        │ dish_aliases │
     │   │      └─────────────────────┘        └──────┬───────┘
     │   │                                            │ dish_id
     │   │  ┌────────────────────┐  ┌────────────────────┐
     │   ├─▶│ restaurant_sources │  │ restaurant_aliases │
     │   │  └────────────────────┘  └────────────────────┘
     │   │  ┌─────────────────────┐
     │   └─▶│ restaurant_scores   │
     │      └─────────────────────┘
     ▼
 ┌─────────┐    ┌────────────────┐
 │ reviews │───▶│ review_sources │
 └────┬────┘    └────────────────┘
      │
      ├──▶ ┌───────────────────────┐     ┌────────────────┐
      │    │ review_dish_mentions  │────▶│ review_aspects │
      │    └───────────────────────┘     └────────────────┘
      │
      ├──▶ ┌───────┐   ┌──────────────────┐
      │    │ likes │   │ moderation_queue │
      │    └───────┘   └──────────────────┘
      ▼
 ┌───────┐    ┌──────────┐    ┌───────────┐   ┌─────────────┐
 │ users │───▶│ profiles │───▶│ bookmarks │──▶│ collections │
 └───┬───┘    └──────────┘    └───────────┘   └─────────────┘
     │
     ├──▶ ┌─────────────┐   ┌────────────────────┐
     │    │ user_badges │   │ gamification_events│
     │    └─────────────┘   └────────────────────┘
     │
     └──▶ (author of reviews)

 ┌─────────────────┐  ┌───────────────────┐  ┌───────────────┐
 │ ingestion_jobs  │  │ ai_processing_jobs│  │ trend_metrics │
 └─────────────────┘  └───────────────────┘  └───────────────┘
 ┌───────────────────┐
 │ entity_conflicts  │
 └───────────────────┘
```

---

## 2. Table reference

### cities
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| name | text | `Kolkata` |
| slug | text UNIQUE | `kolkata` |
| country | text | ISO-3166 alpha-2 |
| center | `geography(Point,4326)` | |
| lat, lng | double precision | denormalized for cheap serialization |
| radius_m | int | ingestion bbox radius, default 25000 |
| timezone | text | `Asia/Kolkata` |
| active | bool | soft launch gate |

### restaurants
`id, city_id FK, name, slug, normalized_name, location geography(Point,4326), lat, lng,
address, area, cuisines text[], price_level smallint (1..4), phone, website,
osm_type, osm_id, is_closed, is_verified, data_confidence numeric,
review_count int, first_seen_at, last_ingested_at`

- `UNIQUE (city_id, slug)`
- `GIST (location)` — spatial index for `ST_DWithin` distance filters
- `GIN (normalized_name gin_trgm_ops)` — fuzzy entity resolution
- `GIN (cuisines)`
- `UNIQUE (osm_type, osm_id) WHERE osm_id IS NOT NULL`

### restaurant_sources
Provenance + idempotency anchor.
`id, restaurant_id FK CASCADE, source (enum: osm|reddit|youtube|user|manual),
external_id, url, raw jsonb, content_hash, fetched_at`
- `UNIQUE (source, external_id)`

### restaurant_aliases
`id, restaurant_id FK CASCADE, alias, normalized_alias, source, confidence`
- `UNIQUE (restaurant_id, normalized_alias)`, `GIN (normalized_alias gin_trgm_ops)`

### dishes
Canonical, **city-agnostic** dish concepts.
`id, name, slug UNIQUE, normalized_name, cuisine, category (enum), is_veg bool NULL,
description, hero_image_url, aliases_count, mention_count, search_vector tsvector GENERATED`
- `GIN (search_vector)`, `GIN (normalized_name gin_trgm_ops)`

`is_veg` is nullable on purpose: `momo` can be either.

### dish_aliases
Drives extraction. `id, dish_id FK CASCADE, alias, normalized_alias, lang, weight numeric`
- `UNIQUE (normalized_alias, lang)` — one alias resolves to exactly one dish per language
- Seeded with Bengali/Hindi transliterations: `momo, mo mo, মোমো, dimsum`.

### restaurant_dishes
The dish-first bridge; one row per (restaurant, dish) that has evidence.
`id, restaurant_id FK CASCADE, dish_id FK CASCADE, mention_count, positive_count,
negative_count, neutral_count, price_min, price_max, price_avg, currency,
first_mentioned_at, last_mentioned_at, is_signature bool`
- `UNIQUE (restaurant_id, dish_id)`

### reviews
Unified row for both ingested and user content.
`id, restaurant_id FK, city_id FK, user_id FK NULL, source enum, lang,
title, body, rating numeric NULL, rating_scale smallint NULL,
author_external, engagement_score int, source_quality numeric,
published_at timestamptz, ingested_at,
content_hash, simhash bigint,
status enum(pending|published|rejected|flagged) DEFAULT pending,
ai_state enum(pending|processing|done|failed) DEFAULT pending,
overall_sentiment numeric NULL, spam_score numeric, is_duplicate bool,
like_count int`
- `UNIQUE (content_hash)` — hard dedupe
- `INDEX (restaurant_id, published_at DESC)`, `INDEX (status, ai_state)`
- `INDEX (simhash)` — near-dupe candidate lookup

`source_quality` ∈ [0,1] is assigned per source at ingest (see ranking.md §4).

### review_sources
`id, review_id FK CASCADE, source, external_id, url, permalink, raw jsonb, license, attribution`
- `UNIQUE (source, external_id)`
- `license`/`attribution` exist so OSM/Reddit terms can be honoured in the UI.

### review_dish_mentions
**The core observation table.**
`id, review_id FK CASCADE, dish_id FK, restaurant_id FK, snippet text,
sentiment numeric (-1..1), confidence numeric, price_mentioned numeric NULL,
is_recommended bool NULL, extraction_method enum(ai|alias|user), created_at`
- `UNIQUE (review_id, dish_id)` — a review counts once per dish
- `INDEX (dish_id, restaurant_id)`, `INDEX (restaurant_id, dish_id)`

### review_aspects
Aspect-level sentiment, optionally tied to a specific dish mention.
`id, review_id FK CASCADE, dish_mention_id FK NULL CASCADE,
aspect enum(taste|portion|price|service|ambience|hygiene|wait_time|consistency|spice),
sentiment numeric, snippet, confidence`

### dish_scores
Materialized ranking output, per `(dish, restaurant, city)`.
`id, dish_id FK, restaurant_id FK, city_id FK,
score numeric, raw_score numeric, sentiment_component … confidence_component (7 cols),
positive_ratio numeric, observed_positivity numeric, mention_count int, evidence_weight numeric,
consistency numeric, recency_days numeric, bayesian_score numeric,
price_avg numeric, value_score numeric,
is_hidden_gem bool, is_best_value bool, is_most_consistent bool,
trend enum(rising|stable|declining) NULL, trend_delta numeric NULL,
why jsonb, top_attributes text[], status enum(ranked|insufficient_data),
weights_version text, computed_at`
- `UNIQUE (dish_id, restaurant_id, city_id)`
- `INDEX (dish_id, city_id, score DESC)` — the hot path for `/dishes/{slug}/restaurants`
- partial `INDEX (dish_id, city_id, score DESC) WHERE status = 'ranked'`

`why` is structured (`[{code, label, value}]`) so the frontend composes the sentence and
the string is never invented by an LLM.

`observed_positivity` stores the **pre-shrinkage** weighted positivity. Badges are
assigned after a DB round-trip, and "hidden gem" is judged on the unshrunk value —
shrinkage exists to damp exactly the low-volume rows that badge targets, so using the
shrunk score would make it unreachable.

### restaurant_scores
Rollup + Food DNA. `id, restaurant_id UNIQUE, city_id, overall_score, sentiment,
consistency, value_score, price_level, trend, trend_delta, dna jsonb,
top_dish_ids uuid[], evidence_count, status, computed_at`

### ranking_snapshots
Append-only history for auditing rank movement.
`id, dish_id, restaurant_id, city_id, score, rank, mention_count, weights_version, taken_at`
- `INDEX (dish_id, city_id, taken_at DESC)`
- A row is written only when the score moved by `SNAPSHOT_SCORE_DELTA` (default 0.5)
  or mentions changed by `SNAPSHOT_MENTION_DELTA` (default 2) since the last snapshot
  for the pair. The first snapshot for a pair is always written. This keeps the table
  from growing linearly with every refresh cycle.

### trend_metrics
`id, subject_type enum(dish|restaurant|dish_restaurant), dish_id NULL, restaurant_id NULL,
city_id, window_days int, recent_sentiment, historical_sentiment, recent_count,
historical_count, delta, direction enum, significant bool, computed_at`

### users / profiles
`users: id (== Supabase sub uuid), email, role enum(user|moderator|admin),
is_banned, created_at, last_seen_at`
`profiles: user_id PK FK CASCADE, username UNIQUE CITEXT-ish, display_name, avatar_url,
bio, city_id, review_count, like_received_count, contribution_score,
favourite_dish_ids uuid[], favourite_restaurant_ids uuid[]`

`users.id` mirrors the Supabase user id, so no join table is needed.

### bookmarks / bookmark_collections
`bookmark_collections: id, user_id FK CASCADE, name, slug, is_public`
`bookmarks: id, user_id FK CASCADE, collection_id FK NULL SET NULL,
target_type enum(dish|restaurant|dish_restaurant), dish_id NULL, restaurant_id NULL, note`
- `UNIQUE (user_id, target_type, dish_id, restaurant_id)` (NULLS NOT DISTINCT)
- CHECK: exactly the right FK set for each `target_type`

### likes
`id, user_id FK CASCADE, review_id FK CASCADE` — `UNIQUE (user_id, review_id)`

### user_badges / gamification_events
`user_badges: id, user_id, badge_code, level smallint, awarded_at` — `UNIQUE (user_id, badge_code)`
`gamification_events: id, user_id, event_type, points int, dish_id NULL, restaurant_id NULL,
review_id NULL, meta jsonb, created_at`
Points are derived from events, so a spam purge can recompute totals honestly.

### ingestion_jobs / ai_processing_jobs
`ingestion_jobs: id, source, city_id, job_key, status enum(queued|running|success|failed|skipped),
attempt int, params jsonb, items_seen, items_created, items_updated, items_skipped,
error text, started_at, finished_at`
- `UNIQUE (job_key)` where `job_key = f"{source}:{city}:{bucket}"` → idempotent scheduling
`ai_processing_jobs: id, review_id FK CASCADE, status, attempt, provider, model,
tokens_in, tokens_out, latency_ms, error, payload jsonb, created_at, finished_at`

### moderation_queue
`id, review_id FK CASCADE, reason enum(spam|duplicate|abuse|user_report|low_quality|manual),
status enum(open|resolved|dismissed), severity smallint, reporter_user_id NULL,
assignee_user_id NULL, notes, decided_by NULL, decided_at, history jsonb[]`
`history` accumulates `{at, actor, from, to, reason}` so moderation is never lossy.

### entity_conflicts
`id, kind enum(restaurant|dish), city_id, candidate_a uuid, candidate_b uuid,
payload jsonb, similarity numeric, status enum(open|merged|rejected), resolved_by, resolved_at`

---

## 3. Index rationale

| Index | Query it serves |
|---|---|
| `GIST(restaurants.location)` | `ST_DWithin(location, :pt, :radius)` distance filter |
| `(dish_scores.dish_id, city_id, score DESC)` | dish page top-N, the single hottest query |
| `GIN(restaurants.normalized_name trgm)` | entity resolution fuzzy match |
| `GIN(dishes.search_vector)` | full-text dish search |
| `UNIQUE(reviews.content_hash)` | exact-duplicate rejection at write time |
| `(reviews.status, ai_state)` | worker claim query |

## 4. Migrations

Single Alembic head. `0001_initial` creates extensions, enums, tables and indexes.
Enums are created with explicit `sa.Enum(..., name=...)` so downgrade is clean.
Seed data (cities, dishes, dish_aliases) lives in `scripts/seed.py`, **not** in migrations,
so re-seeding never fights schema history.

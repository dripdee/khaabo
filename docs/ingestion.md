# Khaabo — Ingestion Pipeline

All external providers sit behind adapters (`backend/app/ingestion/`) implementing a
single interface, so any source can be replaced or disabled without touching domain code.

```python
class SourceAdapter(ABC):
    source: SourceType
    default_interval_hours: int
    async def discover_places(self, city: City) -> list[RawPlace]: ...
    async def fetch_reviews(self, city: City, since: datetime | None) -> list[RawReview]: ...
```

Legal posture: only OSM/Overpass/Nominatim, the official Reddit API, the official YouTube
Data API, user submissions, and openly licensed datasets. **No Google Maps/Places, no
review scraping, no bypassing robots.txt or ToS.** Attribution and licence strings are
stored per record in `review_sources.license` / `attribution` and rendered in the UI.

---

## 1. Sources

| Adapter | Provides | Interval | Limits respected |
|---|---|---|---|
| `OverpassAdapter` | restaurants, cafes, fast_food, cuisine, price hints, coords | 24 h | 1 req / 2 s, single bbox per run, backoff on 429/504, mirror rotation |
| `NominatimAdapter` | geocode/reverse-geocode, area names | on demand (cached 30 d) | 1 req/s hard, `User-Agent` with contact, results cached in DB |
| `RedditAdapter` | subreddit posts+comments mentioning food/places | 6 h | OAuth script app, ≤ 60 req/min, `after` cursor persistence |
| `YouTubeAdapter` | food-vlog metadata, descriptions, top comments | 24 h | quota budget guard (`search.list` = 100 units), stops before daily cap |
| `UserSubmissionAdapter` | on-platform reviews | near-real-time | per-user rate limits |

`SOURCES_ENABLED=osm,reddit,youtube,user` and `SOURCE_INTERVAL_<NAME>_HOURS` make each
source individually configurable — required interval range is 6–24 h.

### Overpass query (Kolkata bbox from `cities`)

```overpassql
[out:json][timeout:60];
(
  node["amenity"~"restaurant|cafe|fast_food|food_court|ice_cream"](around:25000,22.5726,88.3639);
  way ["amenity"~"restaurant|cafe|fast_food|food_court|ice_cream"](around:25000,22.5726,88.3639);
);
out center tags;
```

### Reddit

Subreddits configurable (`REDDIT_SUBREDDITS=kolkata,india,IndianFood,...`). Only public
listings via the API; comment trees limited to depth 2 and top 50 by score. Posts are
matched against city + dish alias vocabulary before being stored, so unrelated threads are
skipped early (`items_skipped`).

### YouTube

`search.list` with city+dish query templates, then `videos.list` for stats and
`commentThreads.list` for the top comments where enabled. Quota is tracked in Redis with
a daily budget; the job exits `skipped` when the remaining budget is insufficient rather
than blowing the free quota.

---

## 2. Normalization

Each adapter returns `RawPlace` / `RawReview` dataclasses. `app/ingestion/normalize.py`:

- unicode NFKC, strip zero-width chars, collapse whitespace
- lowercase + `unaccent` for `normalized_name`
- name cleanup: drop legal suffixes, `&`→`and`, remove branch markers (`- Salt Lake`)
  into a separate `area` hint
- price: `₹`, `Rs.`, `INR`, `rs 200/-` → numeric
- timestamps → UTC `timestamptz`
- text truncated to 8000 chars for AI safety

## 3. Deduplication (three layers)

1. **Source identity** — `UNIQUE (source, external_id)`. Same item twice = skip.
2. **Exact content** — `sha256` over normalized text + author + timestamp, `UNIQUE` on
   `reviews.content_hash`. Cross-posted identical text is rejected once.
3. **Near duplicate** — 64-bit simhash over weighted unigrams (×2) and bigrams (×1);
   candidates fetched by Hamming distance ≤ 12 within the same restaurant and recent
   history, then confirmed with token-set Jaccard ≥ 0.82. Marked `is_duplicate=true`
   (kept for audit, excluded from ranking) and queued for moderation if user-submitted.

   The two-stage design matters: measured on real review pairs, a single-word edit sits
   at 6–9 bits while genuinely different reviews sit at 17+. Hamming distance alone is
   therefore only a *candidate* filter — Jaccard is what prevents false positives. Two
   reviews of the same dish with opposite verdicts ("thick and not too sweet" vs
   "watery and far too sweet") are 17 bits apart but only 0.69 Jaccard, so they survive
   as two distinct observations.

`test_dedup.py` covers: identical text, whitespace/case/punctuation variants,
near-identical with a changed word, shared-wording-opposite-verdict pairs, and genuinely
different reviews that must **not** collapse.

## 4. Entity resolution

`app/services/entity_resolution.py`, deterministic and ordered:

```text
1. source key hit                      → confidence 1.00
2. exact normalized name + same city   → 0.95
3. alias table hit                     → 0.90
4. trigram sim ≥ 0.82 AND dist ≤ 250 m → 0.60 + 0.4·sim
5. trigram sim ≥ 0.92 AND dist ≤ 1 km  → 0.55        (chains/relocations)
6. else                                → create new, confidence 0.30
```

Guard: if the top two candidates are within `0.05` similarity of each other, **do not
choose** — write `entity_conflicts` and attach the review to the higher-confidence
candidate only if it clears 0.9; otherwise leave it unresolved for admin. Distance uses
PostGIS `ST_DWithin` on the GIST index.

Chain handling: `"Wow! Momo"` at two locations resolves to two restaurants (distance gate)
but shares the alias row, so branch-level ranking stays separate — which is correct, since
one branch can be much better than another.

## 5. Job orchestration

```text
Celery beat
 ├─ ingest.discover_places        every SOURCE_INTERVAL_OSM_HOURS
 ├─ ingest.fetch_reviews(source)  per source interval, per active city
 ├─ ranking.nightly_sweep         03:30 city time
 ├─ trends.recompute              every 6 h
 └─ maintenance.prune_jobs        daily
```

Every run creates an `ingestion_jobs` row with
`job_key = "{source}:{city_slug}:{YYYY-MM-DDTHH}"` under a UNIQUE constraint — a
duplicate schedule tick becomes `skipped` instead of a double fetch.

Retry policy: `autoretry_for=(TransientSourceError, httpx.HTTPError)`,
`retry_backoff=True`, `retry_backoff_max=1800`, `retry_jitter=True`, `max_retries=5`.
Rate-limit responses (`429`, Overpass `504`) raise `TransientSourceError` with
`Retry-After` honoured.

Counters recorded per run: `items_seen, items_created, items_updated, items_skipped`,
plus `error` on failure — this is what the admin "failed jobs" view reads.

## 6. Ordering guarantee

Ingestion never writes derived scores. It writes evidence and enqueues
`ai_processing`, which enqueues `ranking`. So a partial ingestion run can never leave a
half-updated ranking — the worst case is evidence present with `ai_state='pending'`, which
the UI counts as "not enough data yet".

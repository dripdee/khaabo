# Khaabo — API

FastAPI, prefix `/api/v1`. OpenAPI at `/docs`, schema at `/api/v1/openapi.json`.
All responses are Pydantic v2 models; all list endpoints are paginated
(`?page=1&page_size=20`, envelope `{items, page, page_size, total, has_more}`).

Auth: `Authorization: Bearer <supabase_access_token>`.
Roles: `user` < `moderator` < `admin`.

---

## 1. Endpoint map

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | – | liveness + component checks |
| GET | `/cities` | – | active cities |
| GET | `/search` | optional | unified dish/restaurant/cuisine search |
| GET | `/search/suggest` | – | typeahead |
| GET | `/dishes` | – | browse/filter dishes |
| GET | `/dishes/{slug}` | – | dish page payload |
| GET | `/dishes/{slug}/restaurants` | – | ranked list + badges + why |
| GET | `/dishes/{slug}/map` | – | GeoJSON-ish markers for the dish |
| GET | `/dishes/{slug}/summary` | – | evidence-based summary |
| GET | `/restaurants` | – | list/filter |
| GET | `/restaurants/{id}` | – | detail |
| GET | `/restaurants/{id}/food-dna` | – | DNA chips + components |
| GET | `/restaurants/{id}/dishes` | – | dishes ranked within restaurant |
| GET | `/restaurants/{id}/reviews` | – | published reviews only |
| GET | `/trending` | – | rising dishes / restaurants |
| POST | `/reviews` | user | rate-limited, async AI |
| GET | `/reviews/{id}` | – | published, or own, or moderator |
| DELETE | `/reviews/{id}` | owner/mod | soft reject |
| POST | `/reviews/{id}/report` | user | → moderation_queue |
| POST | `/likes` | user | idempotent toggle |
| DELETE | `/likes/{review_id}` | user | |
| POST | `/bookmarks` | user | idempotent |
| DELETE | `/bookmarks/{id}` | owner | |
| GET | `/bookmarks` | user | own, filter by collection |
| POST | `/collections` | user | |
| GET | `/users/me` | user | JIT-provisions local row |
| PATCH | `/users/me` | user | profile edit |
| GET | `/users/{username}` | – | public profile |
| GET | `/admin/*` | admin/mod | see §5 |

---

## 2. Search

`GET /search?q=best+chicken+momo+under+300+near+salt+lake`

The query string is parsed by `app/services/query_parser.py` into structured intent
before any DB work:

```json
{
  "raw": "best chicken momo under 300 near salt lake",
  "dish_terms": ["chicken momo"],
  "cuisine": null,
  "area": "Salt Lake",
  "max_price": 300,
  "dietary": null,
  "mood": null,
  "intent": "dish",
  "superlative": true
}
```

Recognized modifiers: `under/below ₹N`, `cheap|budget|affordable` → price band,
`near me` (uses `lat`/`lng` params), `near <area>` (Nominatim-cached area lookup),
`veg|vegan|halal|jain|egg` → dietary, `for working|studying|date|late night` → mood,
`best|top` → superlative (sort by score, require ranked status).

Explicit params always override parsed ones: `city, lat, lng, radius_m, dish, cuisine,
area, min_price, max_price, dietary, mood, open_now, sort (score|distance|trending|price),
trend, page, page_size`.

Response is intent-shaped:

```json
{
  "intent": "dish",
  "parsed": { "...": "as above" },
  "dishes": [ { "slug":"chicken-momo", "name":"Chicken Momo", "score":88.4,
                "trend":"rising", "restaurant_count":37, "price_range":[60,220] } ],
  "restaurants": [ { "id":"…", "name":"…", "dish_score":91.2, "distance_m":740,
                     "why":[{"code":"positive_ratio","label":"91% positive dish sentiment"}] } ],
  "page":1, "page_size":20, "total":37, "has_more":true
}
```

Implementation is Postgres FTS (`tsvector` + `pg_trgm` fallback) behind
`SearchBackend` ABC → `PostgresSearchBackend`. Adding OpenSearch later means adding one
class and flipping `SEARCH_BACKEND`.

---

## 3. Dish page contracts

`GET /dishes/chicken-momo?city=kolkata&lat=&lng=`

```json
{
  "dish": {"slug":"chicken-momo","name":"Chicken Momo","cuisine":"Tibetan",
           "category":"street_food","is_veg":false},
  "city": {"slug":"kolkata","name":"Kolkata"},
  "score": 88.4,
  "status": "ranked",
  "trend": {"direction":"rising","delta":0.11,"significant":true},
  "mention_count": 412,
  "restaurant_count": 37,
  "price_range": {"min":60,"max":220,"avg":118,"currency":"INR"},
  "positive_attributes": [{"label":"juicy","count":61},{"label":"spicy","count":44}],
  "negative_attributes": [{"label":"oily","count":12}],
  "summary": {"text":"…","evidence_review_ids":["…"],"generated_by":"template|model"},
  "highlights": {
    "top": {"restaurant_id":"…"},
    "best_value": {"restaurant_id":"…"},
    "hidden_gem": {"restaurant_id":"…"},
    "most_consistent": {"restaurant_id":"…"}
  },
  "recent_signals": [{"period":"2026-07","positive_ratio":0.93,"mentions":38}]
}
```

`GET /dishes/chicken-momo/restaurants?sort=score&max_price=300&radius_m=4000`

Each item carries `score`, `positive_ratio`, `mention_count`, `consistency`, `price_avg`,
`trend`, `badges[]`, `why[]`, `distance_m`, `sample_snippets[]` (≤2, verbatim quotes with
source attribution), and `status`. Rows with `status="insufficient_data"` are returned in
a separate `insufficient` array, never interleaved into the ranking.

`GET /dishes/chicken-momo/map` returns marker-optimized payload only
(`id, name, lat, lng, score, price_avg, trend, badges`) so the map does not download
review text.

---

## 4. Writes

`POST /reviews`

```json
{ "restaurant_id":"uuid", "body":"Chicken momo is amazing but biryani is average.",
  "rating":4.5, "dish_hints":["chicken-momo"] }
```

- Body 20–5000 chars, HTML-stripped, control chars rejected.
- `content_hash` collision → `409 duplicate_review`.
- Created as `status=pending`, `ai_state=pending`; returns `202` with the review id.
- Rate limit: 5/hour, 20/day per user; 30/hour per IP.
- Response includes `moderation: {status:"pending", eta_seconds: 60}`.

`POST /likes` / `POST /bookmarks` are idempotent upserts returning the current state, so
the frontend can apply optimistic updates and reconcile without special-casing conflicts.

Errors use a single envelope:

```json
{"error":{"code":"rate_limited","message":"Too many reviews","details":{"retry_after":1800}}}
```

Codes: `validation_error, unauthorized, forbidden, not_found, duplicate_review,
rate_limited, insufficient_data, upstream_unavailable, internal_error`.

---

## 5. Admin

`/admin/restaurants`, `/admin/dishes`, `/admin/reviews`, `/admin/source-records`,
`/admin/ai-outputs`, `/admin/entity-conflicts`, `/admin/moderation`,
`/admin/jobs/failed`, `/admin/ranking`.

Mutations that matter:

| Action | Endpoint |
|---|---|
| Approve/reject/flag review | `POST /admin/moderation/{id}/decide` |
| Merge duplicate restaurants | `POST /admin/restaurants/{id}/merge` |
| Remap a dish mention | `PATCH /admin/reviews/{id}/mentions/{mention_id}` |
| Resolve entity conflict | `POST /admin/entity-conflicts/{id}/resolve` |
| Retry failed job | `POST /admin/jobs/{id}/retry` |
| Force recompute | `POST /admin/ranking/recompute` |

Every admin mutation appends to `moderation_queue.history` or an audit log with actor id.

---

## 6. Cross-cutting

- **Rate limiting**: Redis fixed-window per `(user|ip, route-class)`; write routes are
  strict, read routes generous. Degrades open if Redis is down (logged).
- **CORS**: explicit origin allowlist from `CORS_ORIGINS`; no `*` with credentials.
- **Validation**: Pydantic v2 everywhere, `extra="forbid"` on request bodies.
- **DB access**: SQLAlchemy 2.0 constructs only — parameterized by construction.
- **Request limits**: 1 MB JSON body, 5 MB upload, 30 s server timeout.
- **Attribution**: any response containing OSM-derived data includes
  `attribution: ["© OpenStreetMap contributors"]`.

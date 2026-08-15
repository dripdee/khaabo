# Khaabo — Ranking Algorithm

Ranking answers *"which restaurant makes the best **chicken momo**"*, not
*"which restaurant has the best average rating"*. Every number below is derived from
stored `review_dish_mentions` rows; nothing is model-generated.

Implementation: `backend/app/services/ranking.py` (pure) + `RankingService` (persistence).
Tests: `backend/tests/test_ranking.py`.

---

## 1. Weights

```text
Score = 0.35·sentiment
      + 0.20·recency
      + 0.15·consistency
      + 0.10·volume
      + 0.10·source_quality
      + 0.05·engagement
      + 0.05·confidence
```

Configurable via env (`RANKING_W_SENTIMENT=0.35`, …). Weights are validated to sum to
1.0 ± 0.001 at startup; a mismatch is a hard config error. Each persisted `dish_scores`
row stores `weights_version` so historical snapshots remain interpretable after a
re-tune.

All seven components are normalized to **[0, 1]** before weighting, so the raw score is
also in [0, 1] and is stored ×100 for display.

---

## 2. Components

### 2.1 Sentiment (35%) — with Bayesian shrinkage

Per-mention sentiment ∈ [-1, 1] is mapped to [0, 1] via `(s + 1) / 2`, then weighted by
mention `confidence` and time decay:

```text
w_i         = confidence_i · decay(age_i) · source_quality_i
weighted_p  = Σ(p_i · w_i) / Σ(w_i)
```

Then shrink toward the **city+dish prior** so 2 glowing mentions cannot outrank 500
consistently good ones:

```text
sentiment = (weighted_p · Σw + prior · m) / (Σw + m)
```

- `prior` = mean positive-ratio for that dish across the city (fallback `0.62`)
- `m` = `RANKING_BAYES_M` (default **6.0**) — the "phantom" evidence mass

Consequence, with prior 0.62 and m = 6:

| evidence | raw p | shrunk sentiment |
|---|---|---|
| 2 mentions, w≈1.6 | 1.00 | 0.70 |
| 40 mentions, w≈32 | 0.90 | 0.86 |
| 500 mentions, w≈400 | 0.88 | 0.88 |

This is the single most important safety property of the ranking, and
`test_two_reviews_cannot_beat_five_hundred` asserts it directly.

### 2.2 Recency (20%) — exponential decay

```text
decay(age_days) = 0.5 ** (age_days / RANKING_HALFLIFE_DAYS)      # default 180
recency         = decay(days_since_latest_mention) clipped to [0,1]
```

A dish-restaurant whose last mention is 6 months old scores 0.5 on recency; at 18 months
it is 0.125. Recency never zeroes the entry — old evidence still counts, it just fades.

**Sufficiency is judged on undecayed weight.** Age is already priced into this component,
so letting decay also gate the `insufficient_data` threshold would double-count it and
make a well-documented older dish vanish instead of merely ranking lower.

### 2.3 Consistency (15%)

Dispersion of sentiment, not its mean:

```text
consistency = 1 - min(1, stdev(p_i) / 0.5)
```

With < 3 mentions, `consistency = 0.5` (unknown, not perfect). A place with
`[0.95, 0.92, 0.94]` beats `[1.0, 1.0, 0.2]` on this component even at equal means —
which is exactly the "most consistent" badge.

### 2.4 Volume (10%) — log saturation

```text
volume = ln(1 + n) / ln(1 + RANKING_VOLUME_SATURATION)     # default 50, clipped to 1
```

Saturating: mention 51 through 500 add almost nothing, so a viral thread cannot buy rank.

### 2.5 Source quality (10%)

Evidence-weighted mean of per-source trust:

| source | quality | reasoning |
|---|---|---|
| `user` (verified account) | 0.90 | on-platform, moderated, attributable |
| `reddit` | 0.75 | discursive, real experience, karma signal |
| `youtube` | 0.60 | engagement-biased, promo risk |
| `osm` | 0.40 | metadata not opinion |
| `manual` | 0.85 | admin-curated |

Unmoderated/new-account user reviews are damped to 0.6 until the account has an accepted
review.

### 2.6 Engagement (5%)

```text
engagement = ln(1 + Σ engagement_score) / ln(1 + 500)
```

`engagement_score` = Reddit upvotes, YouTube likes, or on-platform review likes.

### 2.7 Confidence (5%)

Mean extraction confidence, penalized when the extraction was alias-only rather than
model-verified, and when `n < 3`.

---

## 3. Insufficient data

If, after aggregation:

```text
mention_count < RANKING_MIN_MENTIONS (default 3)
  OR total_weight < RANKING_MIN_WEIGHT (default 1.5)
```

then the row is persisted with `status = 'insufficient_data'`, `score = NULL`, and it is
**excluded from ranked listings**. The API surfaces such rows only in an explicit
`insufficient` bucket, and the UI shows *"Not enough data"*. There is no partial-credit
fake rank.

---

## 4. Derived badges

| Badge | Rule |
|---|---|
| **Best value** | max `value_score = sentiment · price_factor`, `price_factor = clamp(median_city_price / price_avg, 0.5, 1.6)`; requires a price signal |
| **Hidden gem** | `observed_positivity ≥ 0.85` AND `mention_count` in bottom tercile of the dish AND `consistency ≥ 0.6` |
| **Most consistent** | max `consistency` among rows with `mention_count ≥ 5` |
| **Signature dish** | dish's mention share ≥ 25% of the restaurant's total mentions |

Badges are **relative to the peer group**, so they are assigned after every pair score
for the dish exists — not during individual scoring.

"Hidden gem" is judged on `observed_positivity` (pre-shrinkage) rather than the final
sentiment component. Shrinkage deliberately damps low-volume entries, which is exactly
the population this badge is meant to surface; scoring it on the shrunk value would make
the badge unreachable by construction.

---

## 5. Trend detection

`backend/app/services/trends.py`

```text
recent      = mentions in [now - 60d, now]
historical  = mentions in [now - 240d, now - 60d]
delta       = mean(recent.p) - mean(historical.p)
```

Direction:

```text
delta >  +0.08  → rising
delta <  -0.08  → declining
otherwise       → stable
```

Gate — a trend is emitted **only** if `len(recent) ≥ 3` and `len(historical) ≥ 3`,
else `direction = null` and the UI renders no arrow.

Volume shifts also count, but with a stricter gate: a ≥3× increase in the *per-day*
mention rate at flat sentiment yields `rising` flagged `significant = false`, and only
when the recent window holds ≥10 observations. The windows have different lengths, so
raw counts are normalized to per-day rates first — comparing them directly would report
a surge that does not exist.

Windows are env-configurable (`TREND_RECENT_DAYS`, `TREND_HISTORICAL_DAYS`,
`TREND_DELTA_THRESHOLD`, `TREND_MIN_OBSERVATIONS`).

---

## 6. The "Why?" string

Never LLM-written. `dish_scores.why` stores structured reason codes:

```json
[
  {"code": "positive_ratio", "label": "91% positive dish sentiment", "value": 0.91},
  {"code": "recent",         "label": "strong recent reviews",       "value": 0.84},
  {"code": "consistency",    "label": "consistent quality",          "value": 0.79},
  {"code": "mentions",       "label": "42 dish mentions",            "value": 42}
]
```

The frontend joins labels with ` · `. Each code maps to a fixed template with the number
substituted, so the explanation is a rendering of the score, not a claim about the world.
Only reason codes whose component actually contributed above its median are included —
capped at 4 — so the sentence stays honest and short.

---

## 7. Dish-level aggregate

A dish's own score (used on `/trending` and dish cards) is the weight-averaged score of
its top 10 restaurant rows, so one excellent outlier does not make the whole dish look
citywide-excellent.

---

## 8. Recompute triggers

| Trigger | Scope |
|---|---|
| AI job completes | the exact `(dish, restaurant)` pairs it touched |
| Review published/rejected by moderator | pairs referenced by that review |
| Nightly sweep (03:30 city time) | all rows stale > 24 h |
| Weight change (`weights_version` bump) | full recompute, one-off task |

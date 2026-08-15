# Khaabo — AI Pipeline

The pipeline turns free-text into **structured, attributable observations**. It never
produces prose that is presented as fact, and it never invents a restaurant, dish, price
or rating.

Code: `backend/app/ai/`. Tests: `backend/tests/test_ai_pipeline.py`.

---

## 1. Provider abstraction

```python
class AIProvider(ABC):
    name: str
    async def analyze_review(self, req: ReviewAnalysisRequest) -> ReviewAnalysis: ...
    async def health(self) -> bool: ...
```

| Provider | When used | Cost |
|---|---|---|
| `HeuristicProvider` | default; always available | free, no network |
| `OllamaProvider` | `AI_PROVIDER=ollama` (e.g. `llama3.1:8b`, `qwen2.5:7b`) | free, local |
| `OpenAICompatProvider` | `AI_PROVIDER=openai_compat` — any OpenAI-shaped endpoint | user's choice |

Resolution happens in `app/ai/factory.py`. **Any provider failure falls back to
`HeuristicProvider`** rather than dropping the review, and the fallback is recorded in
`ai_processing_jobs.provider`. There is no paid dependency anywhere on the critical path.

---

## 2. Stages

```text
 raw review text
       │
 1. normalize        strip markup/emoji-noise, collapse whitespace, keep original
       │
 2. language detect  heuristic script+stopword detector (en/bn/hi + romanized)
       │
 3. spam / quality   length, link density, repetition, contact-info, dupe-of-recent
       │
 4. restaurant link  already known for user reviews; for Reddit/YT → entity resolution
       │
 5. dish extraction  alias matcher (dish_aliases, trigram + word-boundary) ∪ model
       │
 6. per-dish analysis  sentiment ∈[-1,1] · attributes · aspects · price · recommend?
       │
 7. overall sentiment  weighted mean of dish sentiments, plus non-dish aspects
       │
 8. persist          review_dish_mentions + review_aspects, emit dirty pairs
```

Stages 1–3 and the alias half of 5 are deterministic Python — so the product works with
**zero** model infrastructure. The model refines: it disambiguates negation
(`"momo was not great"`), scopes clauses, and catches dishes with no alias row.

---

## 3. Structured output contract

`app/ai/schemas.py`, Pydantic v2. The model is *only* ever asked for this shape, and
output is validated + repaired (one retry with the validation error appended) before use.

```python
class DishMentionOut(BaseModel):
    dish_name: str
    matched_alias: str | None
    snippet: str = Field(max_length=280)
    sentiment: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    attributes: list[str] = []          # spicy, juicy, oily, fresh, generous_portion...
    price_mentioned: float | None = None
    is_recommended: bool | None = None
    aspects: list[AspectOut] = []

class ReviewAnalysis(BaseModel):
    language: str
    is_spam: bool
    spam_score: float = Field(ge=0, le=1)
    is_duplicate: bool = False
    overall_sentiment: float | None = Field(default=None, ge=-1, le=1)
    value_signal: Literal["cheap","fair","expensive","unknown"] = "unknown"
    dish_mentions: list[DishMentionOut] = []
    aspects: list[AspectOut] = []
    provider: str
    model: str | None = None
```

### Anti-hallucination rules (enforced in code, not in the prompt)

1. `dish_name` must resolve to an existing `dishes`/`dish_aliases` row, or be created
   only when `AI_ALLOW_DISH_CREATION=true` **and** confidence ≥ 0.8. Unresolvable names
   are dropped and logged.
2. `snippet` must be a substring of the normalized review (fuzzy ≥ 0.9). If not, the
   mention is kept but `snippet` is set to `None` — a quote is never fabricated.
3. `price_mentioned` is accepted only if a currency/number token exists in the text.
4. Sentiment is clamped; a mention with `confidence < 0.35` is discarded.
5. `is_recommended` is ignored unless supported by sentiment sign.

---

## 4. Multi-dish handling

Input: `"Chicken momo is amazing but the biryani is average."`

Output → **two** `review_dish_mentions`:

| dish | sentiment | snippet | aspects |
|---|---|---|---|
| chicken momo | +0.90 | "Chicken momo is amazing" | taste +0.9 |
| biryani | +0.05 | "the biryani is average" | taste 0.0 |

Clause splitting on `but / however / though / although / whereas / যদিও / lekin` plus
comma-clauses is done deterministically first, so even the heuristic provider gets this
canonical case right. `test_multi_dish_opposing_sentiment` locks it.

---

## 5. Summarization (separate queue)

`app/services/summaries.py` builds dish/restaurant summaries **extractively**:

- inputs are only the stored top snippets, attribute counts and score components
- the prompt receives a JSON evidence bundle and is instructed to use nothing else
- output is validated: every claimed attribute must appear in the input bundle, else that
  sentence is dropped
- with no model available, a template composes the summary from counts:
  *"42 mentions · 91% positive · frequently described as juicy, spicy, generous portion."*

Because of this, a summary can be traced back to specific `review_dish_mentions` ids,
which are stored alongside it.

---

## 6. Job lifecycle

```text
review.ai_state: pending → processing → done | failed
```

- Worker claims with `SELECT ... FOR UPDATE SKIP LOCKED` — safe with N concurrent workers.
- Retries: 5 attempts, exponential backoff `2^attempt · 15 s`, jitter, then `failed`
  (visible in admin → failed jobs).
- Idempotent: re-processing deletes that review's prior mentions/aspects inside the same
  transaction before inserting, so a retry can never double-count evidence.
- On success it enqueues `ranking.recompute_pairs(dirty_pairs)`.

## 7. Cost & quota discipline

- Only `ai_state='pending'` rows are ever sent to a model.
- Unchanged `content_hash` ⇒ no reprocessing.
- Batching: up to `AI_BATCH_SIZE` (8) reviews per model call for short texts.
- YouTube/Reddit ingestion caps per run keep well inside free quotas
  (`YOUTUBE_DAILY_QUOTA_UNITS`, `REDDIT_REQUESTS_PER_MINUTE`).

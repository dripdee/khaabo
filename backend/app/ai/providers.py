"""Local model providers (Ollama and any OpenAI-compatible server).

Both are free/self-hostable. Both:
* constrain the model to a closed dish vocabulary in the prompt
* request strict JSON and validate it against `ReviewAnalysis`
* retry **once** with the validation error appended (repair pass)
* fall back to `HeuristicProvider` on any failure, so a review is never lost

The heuristic result is also merged in as a floor: anything the deterministic
extractor found and the model missed is retained.
"""

from __future__ import annotations

import json
import time

import httpx

from app.ai.base import AIProvider
from app.ai.heuristic import HeuristicProvider
from app.ai.schemas import ReviewAnalysis, ReviewAnalysisRequest
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """You extract structured facts from restaurant reviews.

Rules you must follow exactly:
1. Only report dishes that appear in the review text. Never infer a dish from the
   restaurant's cuisine or reputation.
2. Prefer dish names from the ALLOWED DISHES list. If a dish in the text is not in
   the list, use the exact words from the text.
3. `snippet` must be an exact substring of the review text. Never paraphrase it.
4. If a review mentions several dishes with different opinions, output one entry per
   dish with its own sentiment.
5. Only set `price_mentioned` if a currency amount appears in the text.
6. sentiment is a number from -1 (terrible) to 1 (excellent). Use 0 for neutral.
7. Return ONLY a single JSON object. No markdown, no commentary.

JSON shape:
{"language":"en","is_spam":false,"spam_score":0.0,"overall_sentiment":0.5,
 "value_signal":"cheap|fair|expensive|unknown",
 "dish_mentions":[{"dish_name":"","matched_alias":null,"snippet":"","sentiment":0.0,
   "confidence":0.0,"attributes":[],"price_mentioned":null,"is_recommended":null,
   "aspects":[{"aspect":"taste","sentiment":0.0,"confidence":0.0,"snippet":null}]}],
 "aspects":[{"aspect":"service","sentiment":0.0,"confidence":0.0,"snippet":null}]}

Valid aspect values: taste, portion, price, service, ambience, hygiene, wait_time,
consistency, spice.
"""


def build_user_prompt(request: ReviewAnalysisRequest) -> str:
    allowed = ", ".join(sorted(set(request.known_dishes))[:150]) or "(none provided)"
    parts = [f"ALLOWED DISHES: {allowed}"]
    if request.restaurant_name:
        parts.append(f"RESTAURANT: {request.restaurant_name}")
    if request.rating is not None and request.rating_scale:
        parts.append(f"USER RATING: {request.rating}/{request.rating_scale}")
    if request.title:
        parts.append(f"TITLE: {request.title}")
    parts.append(f"REVIEW TEXT:\n{request.text}")
    return "\n\n".join(parts)


def _extract_json(raw: str) -> dict:
    """Models wrap JSON in prose or fences more often than not; recover the object."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _merge_with_heuristic(
    model_analysis: ReviewAnalysis, fallback: ReviewAnalysis
) -> ReviewAnalysis:
    """Union the dish mentions, model result winning on conflict.

    The deterministic extractor is a floor, not a competitor: if an alias clearly
    appears in the text and the model omitted it, keeping it is strictly better than
    silently losing evidence.
    """
    by_name = {(m.matched_alias or m.dish_name).lower(): m for m in fallback.dish_mentions}
    for mention in model_analysis.dish_mentions:
        by_name[(mention.matched_alias or mention.dish_name).lower()] = mention

    return model_analysis.model_copy(
        update={
            "dish_mentions": list(by_name.values()),
            "spam_score": max(model_analysis.spam_score, fallback.spam_score),
            "is_spam": model_analysis.is_spam or fallback.is_spam,
            "overall_sentiment": (
                model_analysis.overall_sentiment
                if model_analysis.overall_sentiment is not None
                else fallback.overall_sentiment
            ),
            "aspects": model_analysis.aspects or fallback.aspects,
        }
    )


class _JSONModelProvider(AIProvider):
    """Shared retry/repair/fallback logic for JSON-emitting chat models."""

    async def _complete(self, system: str, user: str) -> str:  # pragma: no cover - I/O
        raise NotImplementedError

    async def analyze_review(self, request: ReviewAnalysisRequest) -> ReviewAnalysis:
        fallback = await HeuristicProvider().analyze_review(request)
        user_prompt = build_user_prompt(request)
        started = time.perf_counter()

        for attempt in (1, 2):
            try:
                raw = await self._complete(SYSTEM_PROMPT, user_prompt)
                payload = _extract_json(raw)
                payload.setdefault("language", request.lang_hint or fallback.language)
                analysis = ReviewAnalysis.model_validate(payload)
                analysis = analysis.model_copy(update={"provider": self.name, "model": self.model})
                log.info(
                    "ai_model_ok",
                    provider=self.name,
                    attempt=attempt,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    mentions=len(analysis.dish_mentions),
                )
                return _merge_with_heuristic(analysis, fallback)
            except Exception as exc:
                if attempt == 1:
                    # Repair pass: show the model exactly what was wrong.
                    user_prompt = (
                        f"{user_prompt}\n\nYour previous reply was invalid: {exc}\n"
                        "Return ONLY the corrected JSON object."
                    )
                    continue
                log.warning(
                    "ai_model_failed_fallback_heuristic",
                    provider=self.name,
                    error=str(exc)[:300],
                )

        return fallback.model_copy(update={"degraded": True, "notes": f"{self.name}_unavailable"})


class OllamaProvider(_JSONModelProvider):
    """Local Ollama. Free, offline, no quota."""

    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model

    async def _complete(self, system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1, "num_predict": 900},
                },
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
            return resp.status_code == 200
        except Exception:
            return False


class OpenAICompatProvider(_JSONModelProvider):
    """Any OpenAI-shaped endpoint (vLLM, llama.cpp server, LM Studio, ...)."""

    name = "openai_compat"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.openai_compat_base_url).rstrip("/")
        self.api_key = api_key or settings.openai_compat_api_key
        self.model = model or settings.openai_compat_model

    async def _complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def health(self) -> bool:
        if not self.base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/models")
            return resp.status_code < 500
        except Exception:
            return False

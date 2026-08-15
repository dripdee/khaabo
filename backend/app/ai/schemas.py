"""Structured AI output contracts.

The model is only ever asked for these shapes, and output is validated before it
touches the database. Validators here are the enforcement point for the
anti-hallucination rules described in docs/ai-pipeline.md §3.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AspectType, ValueSignal


class AspectOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    aspect: AspectType
    sentiment: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    snippet: str | None = Field(default=None, max_length=320)


class DishMentionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dish_name: str = Field(min_length=1, max_length=160)
    matched_alias: str | None = Field(default=None, max_length=160)
    snippet: str | None = Field(default=None, max_length=320)
    sentiment: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    attributes: list[str] = Field(default_factory=list, max_length=12)
    price_mentioned: float | None = Field(default=None, ge=0, le=100000)
    is_recommended: bool | None = None
    aspects: list[AspectOut] = Field(default_factory=list, max_length=12)

    @field_validator("attributes")
    @classmethod
    def _clean_attributes(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            token = value.strip().lower().replace(" ", "_")[:48]
            if token and token not in cleaned:
                cleaned.append(token)
        return cleaned


class ReviewAnalysis(BaseModel):
    """Everything the pipeline learned about one review."""

    model_config = ConfigDict(extra="ignore")

    language: str = Field(default="en", max_length=12)
    is_spam: bool = False
    spam_score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_duplicate: bool = False
    overall_sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    value_signal: ValueSignal = ValueSignal.UNKNOWN
    dish_mentions: list[DishMentionOut] = Field(default_factory=list, max_length=25)
    aspects: list[AspectOut] = Field(default_factory=list, max_length=12)
    provider: str = "heuristic"
    model: str | None = None
    degraded: bool = False
    notes: str | None = None


class ReviewAnalysisRequest(BaseModel):
    """Input bundle. `known_dishes` is the closed vocabulary the model must use."""

    model_config = ConfigDict(extra="forbid")

    review_id: str | None = None
    text: str = Field(min_length=1)
    title: str | None = None
    lang_hint: str | None = None
    restaurant_name: str | None = None
    known_dishes: list[str] = Field(default_factory=list)
    alias_index: dict[str, str] = Field(default_factory=dict)
    rating: float | None = None
    rating_scale: float | None = None


class SummaryEvidence(BaseModel):
    """Evidence bundle for summarization. The model sees nothing else."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    subject_type: Literal["dish", "restaurant"]
    mention_count: int
    positive_ratio: float
    attributes: list[dict] = Field(default_factory=list)
    snippets: list[str] = Field(default_factory=list, max_length=12)
    price_range: dict | None = None
    trend: str | None = None


class SummaryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(max_length=600)
    generated_by: Literal["template", "model"] = "template"

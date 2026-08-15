"""AI package exports."""

from app.ai.base import AIProvider, enforce_grounding
from app.ai.factory import build_provider, get_provider, reset_provider
from app.ai.heuristic import HeuristicProvider
from app.ai.providers import OllamaProvider, OpenAICompatProvider
from app.ai.schemas import (
    AspectOut,
    DishMentionOut,
    ReviewAnalysis,
    ReviewAnalysisRequest,
    SummaryEvidence,
    SummaryOut,
)

__all__ = [
    "AIProvider",
    "AspectOut",
    "DishMentionOut",
    "HeuristicProvider",
    "OllamaProvider",
    "OpenAICompatProvider",
    "ReviewAnalysis",
    "ReviewAnalysisRequest",
    "SummaryEvidence",
    "SummaryOut",
    "build_provider",
    "enforce_grounding",
    "get_provider",
    "reset_provider",
]

"""Provider selection.

Misconfiguration degrades to the heuristic provider with a warning rather than
failing startup: a broken model endpoint must not take the API down.
"""

from __future__ import annotations

from app.ai.base import AIProvider
from app.ai.heuristic import HeuristicProvider
from app.ai.providers import OllamaProvider, OpenAICompatProvider
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_cached: AIProvider | None = None


def build_provider(name: str | None = None) -> AIProvider:
    provider_name = (name or settings.ai_provider or "heuristic").lower()

    if provider_name == "ollama":
        return OllamaProvider()

    if provider_name == "openai_compat":
        if not settings.openai_compat_base_url or not settings.openai_compat_model:
            log.warning("ai_provider_misconfigured_using_heuristic", provider=provider_name)
            return HeuristicProvider()
        return OpenAICompatProvider()

    if provider_name != "heuristic":
        log.warning("ai_provider_unknown_using_heuristic", provider=provider_name)

    return HeuristicProvider()


def get_provider() -> AIProvider:
    global _cached
    if _cached is None:
        _cached = build_provider()
        log.info("ai_provider_selected", provider=_cached.name, model=_cached.model)
    return _cached


def reset_provider() -> None:
    """Test/admin hook so a provider swap does not require a restart."""
    global _cached
    _cached = None

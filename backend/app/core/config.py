"""Application configuration.

Every knob is environment-driven so the same image runs in dev and prod.
Ranking weights are validated at import time: a mis-summed weight vector is a
configuration error, not a silent ranking bug.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_core.core_schema import ValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

AIProviderName = Literal["heuristic", "ollama", "openai_compat"]

_TRUE = {"1", "true", "yes", "y", "on", "t"}
_FALSE = {"0", "false", "no", "n", "off", "f", ""}

BOOL_FIELDS = (
    "debug",
    "log_json",
    "db_echo",
    "auth_dev_bypass",
    "rate_limit_enabled",
    "ai_allow_dish_creation",
    "cache_enabled",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── core ────────────────────────────────────────────────────────────────
    env: str = "development"
    debug: bool = True
    secret_key: str = "dev-insecure-change-me"
    public_base_url: str = "http://localhost:8000"
    api_prefix: str = "/api/v1"
    project_name: str = "Khaabo"
    contact_email: str = "dev@example.com"
    log_level: str = "INFO"
    log_json: bool = False

    cors_origins: str = "http://localhost:5173"

    # ── database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://khaabo:khaabo@localhost:5432/khaabo"
    sync_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    # ── redis / celery ──────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── cities ──────────────────────────────────────────────────────────────
    default_city_slug: str = "kolkata"

    # ── auth ────────────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    auth_dev_bypass: bool = True

    # ── error monitoring ────────────────────────────────────────────────────
    sentry_dsn: str = ""
    sentry_environment: str = ""
    sentry_traces_sample_rate: float = 0.0

    # ── rate limiting ───────────────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_read_per_minute: int = 120
    rate_limit_write_per_minute: int = 20
    rate_limit_reviews_per_hour: int = 5
    rate_limit_reviews_per_day: int = 20

    # ── ai ──────────────────────────────────────────────────────────────────
    ai_provider: AIProviderName = "heuristic"
    ai_batch_size: int = 8
    ai_timeout_seconds: int = 60
    ai_min_mention_confidence: float = 0.35
    ai_allow_dish_creation: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""

    # ── sources ─────────────────────────────────────────────────────────────
    sources_enabled: str = "osm,user"
    source_interval_osm_hours: int = Field(default=24, ge=1, le=168)
    source_interval_reddit_hours: int = Field(default=6, ge=1, le=168)
    source_interval_youtube_hours: int = Field(default=24, ge=1, le=168)

    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_fallback_urls: str = "https://overpass.kumi.systems/api/interpreter"
    overpass_min_interval_seconds: float = 2.0
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_min_interval_seconds: float = 1.0

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "khaabo/0.1"
    reddit_subreddits: str = "kolkata,india,IndianFood"
    reddit_requests_per_minute: int = 55

    youtube_api_key: str = ""
    youtube_daily_quota_units: int = 9000
    youtube_max_videos_per_run: int = 25

    google_places_api_key: str = ""
    # Free cap for the India billing profile is 35,000 Nearby Search Pro
    # requests/month; the budget is deliberately set below it.
    google_places_monthly_limit: int = 30000
    google_places_max_requests_per_run: int = 9000
    google_refresh_interval_hours: int = Field(default=720, ge=1, le=2160)
    # Two-zone sweep geometry: fine cells cover the dense core, coarse cells
    # cover the suburban ring. See google_places.sweep_cells for the math.
    google_places_fine_cell_m: int = Field(default=300, ge=50, le=5000)
    google_places_fine_radius_m: int = Field(default=10000, ge=1000, le=50000)
    google_places_coarse_cell_m: int = Field(default=1000, ge=100, le=10000)

    # ── ranking ─────────────────────────────────────────────────────────────
    ranking_w_sentiment: float = 0.35
    ranking_w_recency: float = 0.20
    ranking_w_consistency: float = 0.15
    ranking_w_volume: float = 0.10
    ranking_w_source_quality: float = 0.10
    ranking_w_engagement: float = 0.05
    ranking_w_confidence: float = 0.05
    ranking_weights_version: str = "v1"
    ranking_halflife_days: float = 180.0
    ranking_bayes_m: float = 6.0
    ranking_bayes_prior: float = 0.62
    ranking_volume_saturation: float = 50.0
    ranking_min_mentions: int = 3
    ranking_min_weight: float = 1.5
    snapshot_score_delta: float = 0.5
    snapshot_mention_delta: int = 2

    # ── trends ──────────────────────────────────────────────────────────────
    trend_recent_days: int = 60
    trend_historical_days: int = 240
    trend_delta_threshold: float = 0.08
    trend_min_observations: int = 3

    # ── search / cache ──────────────────────────────────────────────────────
    search_backend: Literal["postgres", "opensearch"] = "postgres"
    cache_enabled: bool = True
    cache_ttl_search: int = 120
    cache_ttl_dish: int = 300
    cache_ttl_trending: int = 900

    # ── derived ─────────────────────────────────────────────────────────────
    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field
    @property
    def enabled_sources(self) -> list[str]:
        return [s.strip().lower() for s in self.sources_enabled.split(",") if s.strip()]

    @computed_field
    @property
    def reddit_subreddit_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    @computed_field
    @property
    def overpass_endpoints(self) -> list[str]:
        extra = [u.strip() for u in self.overpass_fallback_urls.split(",") if u.strip()]
        return [self.overpass_url, *extra]

    @computed_field
    @property
    def sync_db_url(self) -> str:
        return self.sync_database_url or self.database_url

    @computed_field
    @property
    def user_agent(self) -> str:
        """OSM/Nominatim policy requires an identifiable UA with contact info."""
        return f"{self.project_name}/0.1 (+{self.public_base_url}; {self.contact_email})"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def ranking_weights(self) -> dict[str, float]:
        return {
            "sentiment": self.ranking_w_sentiment,
            "recency": self.ranking_w_recency,
            "consistency": self.ranking_w_consistency,
            "volume": self.ranking_w_volume,
            "source_quality": self.ranking_w_source_quality,
            "engagement": self.ranking_w_engagement,
            "confidence": self.ranking_w_confidence,
        }

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator(*BOOL_FIELDS, mode="before")
    @classmethod
    def _lenient_bool(cls, v: Any, info: ValidationInfo) -> Any:
        """Tolerate unrelated ambient env vars.

        Names like DEBUG are common in developer shells and may hold values such as
        `release`. Failing startup on someone else's variable is unhelpful, so an
        uninterpretable value falls back to the field's declared default.
        """
        if isinstance(v, bool) or v is None:
            return v
        text = str(v).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        field = cls.model_fields.get(info.field_name or "")
        return field.default if field is not None else False

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        total = sum(self.ranking_weights.values())
        if abs(total - 1.0) > 1e-3:
            raise ValueError(
                f"Ranking weights must sum to 1.0, got {total:.4f}. "
                "Check the RANKING_W_* environment variables."
            )
        if self.trend_historical_days <= self.trend_recent_days:
            raise ValueError("TREND_HISTORICAL_DAYS must exceed TREND_RECENT_DAYS")
        if self.is_production:
            if self.auth_dev_bypass:
                raise ValueError("AUTH_DEV_BYPASS must be false in production")
            if self.secret_key == "dev-insecure-change-me":
                raise ValueError("SECRET_KEY must be set in production")
            if len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be at least 32 characters in production")
            if "*" in self.cors_origin_list:
                raise ValueError("Wildcard CORS origin is not allowed in production")
            if self.contact_email in {"", "dev@example.com", "you@example.com"}:
                raise ValueError("CONTACT_EMAIL must be set to a real address in production")
            if self.public_base_url.startswith("http://localhost"):
                raise ValueError("PUBLIC_BASE_URL must be a real public URL in production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

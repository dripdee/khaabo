"""initial schema

Creates extensions, then the full baseline schema from the ORM metadata.

Why metadata-driven for the baseline: it guarantees the initial migration and the
models cannot drift, which is the single most common source of broken first
deploys. Every migration *after* this one must use explicit `op.*` operations so
that changes are reviewable and reversible.

Revision ID: 0001_initial
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.models import Base

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("postgis", "pgcrypto", "pg_trgm", "unaccent")


def upgrade() -> None:
    bind = op.get_bind()

    for ext in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')

    Base.metadata.create_all(bind=bind, checkfirst=True)

    # Trigram index on restaurant name for entity resolution is declared on the
    # model; these are the composite/partial ones that benefit from explicit SQL.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_dish_scores_ranked_lookup
        ON dish_scores (dish_id, city_id, score DESC)
        WHERE status = 'ranked'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_reviews_pending_ai
        ON reviews (ingested_at)
        WHERE ai_state = 'pending'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_restaurants_open
        ON restaurants (city_id)
        WHERE is_closed = false
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_restaurants_open")
    op.execute("DROP INDEX IF EXISTS ix_reviews_pending_ai")
    op.execute("DROP INDEX IF EXISTS ix_dish_scores_ranked_lookup")
    Base.metadata.drop_all(bind=bind, checkfirst=True)
    for enum_name in (
        "source_type",
        "review_status",
        "ai_state",
        "job_status",
        "score_status",
        "trend_direction",
        "dish_category",
        "aspect_type",
        "extraction_method",
        "user_role",
        "bookmark_target",
        "moderation_reason",
        "moderation_status",
        "conflict_kind",
        "conflict_status",
        "trend_subject",
        "value_signal",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

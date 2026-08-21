"""Google Places: rating columns + 'google' source_type value

Adds the nullable aggregate rating columns (star rating + count only, never
review text — Maps Platform ToS) and registers the new source on the
`source_type` enum used by `restaurant_sources` and `review_sources`.

Revision ID: 0003_google_places
Revises: 0002_widen_osm_id
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_google_places"
down_revision: str | None = "0002_widen_osm_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds the baseline from the ORM metadata, so a fresh database already
    # has these columns — guard for idempotence on both fresh and existing DBs.
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("restaurants")
    }
    if "google_rating" not in existing:
        op.add_column("restaurants", sa.Column("google_rating", sa.Float(), nullable=True))
    if "google_rating_count" not in existing:
        op.add_column("restaurants", sa.Column("google_rating_count", sa.Integer(), nullable=True))
    # Postgres forbids using a freshly-added enum value within the same
    # transaction on some versions; commit first.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'google'")


def downgrade() -> None:
    # Enum values are intentionally left alone on rollback: dropping a value
    # requires rewriting every dependent column and is not reversible-safe.
    op.drop_column("restaurants", "google_rating_count")
    op.drop_column("restaurants", "google_rating")

"""Widen restaurants.osm_id to BIGINT

OSM node ids have grown past 2**31, so OSM ingestion fails with
NumericValueOutOfRange on insert.

Revision ID: 0002_widen_osm_id
Revises: 0001_initial
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_widen_osm_id"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE restaurants ALTER COLUMN osm_id TYPE BIGINT USING osm_id::BIGINT")


def downgrade() -> None:
    op.execute("ALTER TABLE restaurants ALTER COLUMN osm_id TYPE INTEGER USING osm_id::INTEGER")

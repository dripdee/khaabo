"""Database-backed review-resolution tests.

Verifies the full `store_review` ladder through a real session: a body that names
exactly one catalog restaurant attaches the review; zero or many names drop it;
the structured hint path still wins when it clears the confidence bar. Skipped
automatically when PostGIS is unavailable (see ``conftest``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.session import SyncSessionLocal
from app.ingestion.base import RawReview
from app.ingestion.pipeline import load_candidates, store_review
from app.models import City, Restaurant
from app.models.enums import SourceType

KOLKATA_LAT, KOLKATA_LNG = 22.5726, 88.3639


def _make_city(session) -> City:
    city = City(
        name="Kolkata",
        slug="kolkata",
        lat=KOLKATA_LAT,
        lng=KOLKATA_LNG,
        center=f"SRID=4326;POINT({KOLKATA_LNG} {KOLKATA_LAT})",
        active=True,
    )
    session.add(city)
    session.flush()
    return city


def _make_restaurant(session, city: City, name: str) -> Restaurant:
    restaurant = Restaurant(
        city_id=city.id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        normalized_name=name.lower(),
        location=f"SRID=4326;POINT({KOLKATA_LNG} {KOLKATA_LAT})",
        lat=KOLKATA_LAT,
        lng=KOLKATA_LNG,
    )
    session.add(restaurant)
    session.flush()
    return restaurant


def _review(text: str, external_id: str) -> RawReview:
    return RawReview(
        source=SourceType.YOUTUBE,
        external_id=external_id,
        text=text,
        published_at=datetime.now(UTC),
    )


@pytest.mark.db
class TestStoreReviewMention:
    """The behaviour the mention fallback was added for."""

    async def test_body_mention_of_one_restaurant_attaches_the_review(self, db_engine):
        with SyncSessionLocal() as session:
            city = _make_city(session)
            arsalan = _make_restaurant(session, city, "Arsalan")
            session.commit()

            review, action = store_review(
                session,
                city,
                _review(
                    "The chicken biryani at Arsalan was amazing, worth every rupee.",
                    "yt:one",
                ),
            )
            session.commit()

            assert action == "created"
            assert review is not None
            assert review.restaurant_id == arsalan.id

    async def test_body_naming_two_restaurants_is_dropped(self, db_engine):
        with SyncSessionLocal() as session:
            city = _make_city(session)
            _make_restaurant(session, city, "Arsalan")
            _make_restaurant(session, city, "Peter Cat")
            session.commit()

            review, action = store_review(
                session,
                city,
                _review(
                    "Arsalan vs Peter Cat - which serves the best biryani in Kolkata?",
                    "yt:two",
                ),
            )

            assert action == "skipped"
            assert review is None

    async def test_body_naming_no_restaurant_is_dropped(self, db_engine):
        with SyncSessionLocal() as session:
            city = _make_city(session)
            _make_restaurant(session, city, "Arsalan")
            session.commit()

            review, action = store_review(
                session,
                city,
                _review("Best street food tour ever, the momos were unreal.", "yt:none"),
            )

            assert action == "skipped"
            assert review is None


@pytest.mark.db
class TestStoreReviewHint:
    """The pre-existing structured-hint path still works after the refactor."""

    async def test_exact_hint_with_close_coordinates_wins(self, db_engine):
        with SyncSessionLocal() as session:
            city = _make_city(session)
            arsalan = _make_restaurant(session, city, "Arsalan")
            session.commit()

            raw = RawReview(
                source=SourceType.YOUTUBE,
                external_id="yt:hint",
                text="A long enough body so the review is not dropped for length.",
                published_at=datetime.now(UTC),
                restaurant_hint="Arsalan",
                lat=KOLKATA_LAT,
                lng=KOLKATA_LNG,
            )
            review, action = store_review(
                session,
                city,
                raw,
                candidates=load_candidates(session, city.id),
            )
            session.commit()

            assert action == "created"
            assert review is not None
            assert review.restaurant_id == arsalan.id

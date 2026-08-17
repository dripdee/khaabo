"""Seed synthetic demo data so the UI has something to render locally.

This exists for development only. It creates restaurants and reviews that are clearly
marked `source=manual`, runs them through the real AI pipeline and the real ranking
service — so what you see locally is produced by the same code as production, not by
hardcoded scores.

    python -m scripts.seed_demo
    python -m scripts.seed_demo --reset
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.logging import configure_logging, get_logger
from app.db.session import db_session, sync_session
from app.models import (
    City,
    Dish,
    DishScore,
    Restaurant,
    RestaurantDish,
    RestaurantScore,
    Review,
    ReviewAspect,
    ReviewDishMention,
)
from app.models.enums import AIState, ReviewStatus, SourceType
from app.services.dedup import review_fingerprint, simhash, to_signed_64
from app.utils.text import normalize_name, slugify

configure_logging()
log = get_logger(__name__)

random.seed(20260815)  # reproducible demo data

# (name, area, lat, lng, cuisines, price_level)
RESTAURANTS = [
    ("Momo Ghar Salt Lake", "Salt Lake", 22.5810, 88.4180, ["tibetan", "chinese"], 1),
    ("Tibet Kitchen", "Park Street", 22.5535, 88.3520, ["tibetan", "nepali"], 2),
    ("Blue Poppy Thakali", "Elgin", 22.5390, 88.3510, ["nepali", "tibetan"], 2),
    ("Wow Momo Sector V", "Sector V", 22.5760, 88.4330, ["tibetan", "fast food"], 1),
    ("Hamro Momo Corner", "Gariahat", 22.5170, 88.3660, ["tibetan"], 1),
    ("Arsalan Park Circus", "Park Circus", 22.5390, 88.3690, ["mughlai", "biryani"], 2),
    ("Aminia Esplanade", "Esplanade", 22.5650, 88.3510, ["mughlai"], 2),
    ("Royal Indian Hotel", "Chitpur", 22.5860, 88.3610, ["mughlai", "awadhi"], 2),
    ("Dada Boudi Biryani", "Barrackpore", 22.7600, 88.3700, ["biryani", "mughlai"], 1),
    ("Kosha Kitchen", "Bhowanipore", 22.5290, 88.3450, ["bengali"], 2),
    ("Bhojohori Manna", "Ballygunge", 22.5230, 88.3660, ["bengali"], 2),
    ("6 Ballygunge Place", "Ballygunge", 22.5250, 88.3640, ["bengali"], 3),
    ("Ramen-ya Kolkata", "New Town", 22.5800, 88.4640, ["japanese"], 3),
    ("Ippudo Bowl", "Sector V", 22.5745, 88.4310, ["japanese", "ramen"], 3),
    ("The Study Cafe", "Jadavpur", 22.4990, 88.3710, ["cafe", "continental"], 2),
    ("Laptop & Latte", "Sector V", 22.5770, 88.4300, ["cafe"], 2),
    ("Flurys Park Street", "Park Street", 22.5525, 88.3515, ["cafe", "bakery"], 3),
    ("Puchka Gully", "College Street", 22.5760, 88.3630, ["bengali", "street food"], 1),
    ("Kusum Rolls", "Park Street", 22.5528, 88.3535, ["bengali", "street food"], 1),
    ("Balwant Singh Dhaba", "Bhowanipore", 22.5300, 88.3430, ["north indian"], 1),
]

# (dish slug, [(restaurant index, quality 0..1, mention count)])
# Quality is the *intended* underlying quality; the actual score comes out of the
# real ranking pipeline, so these are inputs, not results.
DISH_PLAN = {
    "chicken-momo": [
        (0, 0.94, 38),  # excellent, well-known -> likely top
        (1, 0.90, 26),
        (2, 0.88, 9),  # excellent but under-discussed -> hidden gem candidate
        (3, 0.62, 44),  # chain, mediocre, high volume
        (4, 0.86, 7),  # cheap + good -> best value candidate
    ],
    "chicken-biryani": [
        (5, 0.92, 51),
        (6, 0.84, 33),
        (7, 0.80, 19),
        (8, 0.88, 41),
    ],
    "kosha-mangsho": [(9, 0.90, 14), (10, 0.86, 22), (11, 0.89, 17)],
    "ramen": [(12, 0.88, 12), (13, 0.74, 9)],
    "cold-coffee": [(14, 0.82, 16), (15, 0.78, 11), (16, 0.80, 21)],
    "puchka": [(17, 0.93, 24), (18, 0.70, 6)],
    "kathi-roll": [(18, 0.91, 35), (17, 0.72, 8)],
    "thali": [(19, 0.85, 13)],
    # Deliberately thin: must render "Not enough data", never a fabricated rank.
    "sushi": [(12, 0.80, 2)],
}

POSITIVE_TEMPLATES = [
    "The {dish} here is genuinely excellent, juicy and full of flavour.",
    "Easily the best {dish} I have had in a while. Fresh and perfectly spiced.",
    "Came for the {dish} and it did not disappoint, generous portion too.",
    "{dish} was amazing at ₹{price}, great value for money.",
    "Their {dish} is soft, hot and very tasty. Highly recommend.",
    "Loved the {dish}, authentic and consistently good every visit.",
]
MIXED_TEMPLATES = [
    "The {dish} was decent though nothing special for ₹{price}.",
    "{dish} was okay, service was slow but the food was fine.",
    "Ordered the {dish}, it was average. Ambience is nice though.",
]
NEGATIVE_TEMPLATES = [
    "The {dish} was bland and a bit oily, quite disappointing for ₹{price}.",
    "Not worth it. The {dish} was cold and the portion was small.",
    "{dish} was stale and the service was rude. Avoid.",
]

PRICE_BY_DISH = {
    "chicken-momo": (60, 220),
    "chicken-biryani": (180, 480),
    "kosha-mangsho": (260, 520),
    "ramen": (380, 750),
    "cold-coffee": (120, 320),
    "puchka": (20, 60),
    "kathi-roll": (60, 180),
    "thali": (120, 350),
    "sushi": (450, 900),
}

# Restaurants whose recent reviews are much better/worse, to exercise trends.
RISING = {0, 17}
DECLINING = {3, 6}


async def reset_demo_data() -> None:
    async with db_session() as session:
        await session.execute(delete(ReviewAspect))
        await session.execute(delete(ReviewDishMention))
        await session.execute(delete(DishScore))
        await session.execute(delete(RestaurantDish))
        await session.execute(delete(RestaurantScore))
        await session.execute(delete(Review).where(Review.source == SourceType.MANUAL))
        await session.execute(delete(Restaurant).where(Restaurant.osm_id.is_(None)))
    log.info("demo_data_reset")


async def create_restaurants(city: City) -> list[Restaurant]:
    created: list[Restaurant] = []
    async with db_session() as session:
        for name, area, lat, lng, cuisines, price_level in RESTAURANTS:
            slug = slugify(name)
            existing = (
                await session.execute(
                    select(Restaurant).where(Restaurant.city_id == city.id, Restaurant.slug == slug)
                )
            ).scalar_one_or_none()

            if existing is not None:
                created.append(existing)
                continue

            restaurant = Restaurant(
                city_id=city.id,
                name=name,
                slug=slug,
                normalized_name=normalize_name(name),
                location=f"SRID=4326;POINT({lng} {lat})",
                lat=lat,
                lng=lng,
                area=area,
                cuisines=cuisines,
                price_level=price_level,
                data_confidence=0.9,
                is_verified=True,
                first_seen_at=datetime.now(UTC),
            )
            session.add(restaurant)
            await session.flush()
            created.append(restaurant)

    return created


def _pick_template(quality: float) -> tuple[str, float]:
    roll = random.random()
    if roll < quality:
        return random.choice(POSITIVE_TEMPLATES), 1.0
    if roll < quality + (1 - quality) * 0.6:
        return random.choice(MIXED_TEMPLATES), 0.0
    return random.choice(NEGATIVE_TEMPLATES), -1.0


async def create_reviews(city: City, restaurants: list[Restaurant]) -> int:
    total = 0

    async with db_session() as session:
        for dish_slug, plan in DISH_PLAN.items():
            dish = (
                await session.execute(select(Dish).where(Dish.slug == dish_slug))
            ).scalar_one_or_none()
            if dish is None:
                log.warning("demo_dish_missing", slug=dish_slug)
                continue

            low, high = PRICE_BY_DISH.get(dish_slug, (100, 300))

            for restaurant_index, quality, count in plan:
                restaurant = restaurants[restaurant_index]

                for i in range(count):
                    # Spread across ~14 months so the trend windows have data.
                    days_ago = random.randint(1, 420)

                    effective_quality = quality
                    if restaurant_index in RISING and days_ago <= 60:
                        effective_quality = min(0.98, quality + 0.25)
                    elif restaurant_index in RISING:
                        effective_quality = max(0.2, quality - 0.25)
                    elif restaurant_index in DECLINING and days_ago <= 60:
                        effective_quality = max(0.15, quality - 0.35)

                    template, _ = _pick_template(effective_quality)
                    price = random.randint(low, high)
                    body = template.format(dish=dish.name.lower(), price=price)

                    # Make each row textually distinct so near-duplicate detection
                    # does not (correctly) collapse the demo corpus.
                    body = f"{body} Visit #{i + 1} at {restaurant.name}."

                    fingerprint = review_fingerprint(body, f"demo-{restaurant_index}-{i}", None)
                    exists = (
                        await session.execute(
                            select(Review.id).where(Review.content_hash == fingerprint)
                        )
                    ).first()
                    if exists:
                        continue

                    published = datetime.now(UTC) - timedelta(days=days_ago)
                    session.add(
                        Review(
                            restaurant_id=restaurant.id,
                            city_id=city.id,
                            source=SourceType.MANUAL,
                            body=body,
                            author_external=f"demo_user_{random.randint(1, 250)}",
                            engagement_score=random.randint(0, 40),
                            source_quality=0.85,
                            published_at=published,
                            ingested_at=datetime.now(UTC),
                            content_hash=fingerprint,
                            simhash=to_signed_64(simhash(body)),
                            status=ReviewStatus.PUBLISHED,
                            ai_state=AIState.PENDING,
                        )
                    )
                    total += 1

                restaurant.review_count = (restaurant.review_count or 0) + count

    return total


async def process_and_rank() -> dict:
    """Run AI extraction + ranking locally so the seeded data is immediately ranked.

    Normally Celery handles this asynchronously. For a deterministic, instant demo
    experience, we drain the pending queue in-process.
    """
    from app.services.ai_processing import claim_pending_reviews, process_review
    from app.services.ranking_service import recompute_pairs, recompute_trends

    processed = 0
    pairs: set[tuple] = set()

    while True:
        with sync_session() as session:
            batch = claim_pending_reviews(session, limit=100)
            if not batch:
                break
            for review in batch:
                result = await process_review(session, review)
                pairs.update(result.pairs)
                processed += 1

    with sync_session() as session:
        summary = recompute_pairs(session, pairs)
        trends = recompute_trends(session)

    return {
        "processed": processed,
        "pairs": summary.pairs,
        "ranked": summary.ranked,
        "insufficient": summary.insufficient,
        "trends": trends,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic demo data")
    parser.add_argument("--city", default="kolkata")
    parser.add_argument("--reset", action="store_true", help="Delete existing demo data first")
    parser.add_argument("--skip-processing", action="store_true")
    args = parser.parse_args()

    async with db_session() as session:
        city = (
            await session.execute(select(City).where(City.slug == args.city))
        ).scalar_one_or_none()
        if city is None:
            print(f"City '{args.city}' not found. Run: python -m scripts.seed --city {args.city}")
            return 1
        city_id, city_name = city.id, city.name

    if args.reset:
        await reset_demo_data()

    async with db_session() as session:
        city = (await session.execute(select(City).where(City.id == city_id))).scalar_one()
        restaurants = await create_restaurants(city)

    async with db_session() as session:
        city = (await session.execute(select(City).where(City.id == city_id))).scalar_one()
        fresh = (
            (
                await session.execute(
                    select(Restaurant)
                    .where(Restaurant.city_id == city.id)
                    .order_by(Restaurant.created_at)
                )
            )
            .scalars()
            .all()
        )
        by_slug = {r.slug: r for r in fresh}
        ordered = [by_slug[slugify(name)] for name, *_ in RESTAURANTS]
        review_count = await create_reviews(city, ordered)

    print(f"Created/verified {len(restaurants)} restaurants and {review_count} reviews.")

    if args.skip_processing:
        print("Skipped AI processing. Run the worker or `ai.process_pending` to rank.")
        return 0

    print("Running AI extraction and ranking (this uses the real pipeline)...")
    stats = await process_and_rank()
    print(
        f"Processed {stats['processed']} reviews -> {stats['pairs']} dish/restaurant pairs "
        f"({stats['ranked']} ranked, {stats['insufficient']} insufficient), "
        f"{stats['trends']} trend metrics."
    )
    print(f"\nOpen http://localhost:5173 and search for 'best chicken momo' in {city_name}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

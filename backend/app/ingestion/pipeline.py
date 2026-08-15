"""Ingestion persistence: normalize → resolve → dedupe → store.

Runs in the Celery worker with a **sync** session. Every write path is idempotent:
* places are keyed by `(source, external_id)` on `restaurant_sources`
* reviews are keyed by `content_hash` (UNIQUE) and `(source, external_id)`
* unchanged payload hashes short-circuit before any AI work is queued

Nothing here computes scores. It stores evidence and marks `ai_state='pending'`,
so a partial run can never leave a half-updated ranking.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.base import CityRef, RawPlace, RawReview
from app.models import (
    City,
    EntityConflict,
    Restaurant,
    RestaurantAlias,
    RestaurantSource,
    Review,
    ReviewSource,
)
from app.models.enums import AIState, ConflictKind, ReviewStatus, SourceType
from app.services.dedup import review_fingerprint, simhash, to_signed_64
from app.services.entity_resolution import (
    CandidateRestaurant,
    IncomingPlace,
    MatchMethod,
    resolve_candidate,
)
from app.services.ranking import source_quality_for
from app.utils.text import clean_text, content_hash, normalize_name, slugify

log = get_logger(__name__)


@dataclass(slots=True)
class IngestCounters:
    seen: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    conflicts: int = 0
    review_ids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.review_ids is None:
            self.review_ids = []


def city_ref(city: City) -> CityRef:
    return CityRef(
        id=str(city.id),
        slug=city.slug,
        name=city.name,
        lat=float(city.lat),
        lng=float(city.lng),
        radius_m=int(city.radius_m),
    )


def load_candidates(session: Session, city_id: uuid.UUID) -> list[CandidateRestaurant]:
    """Load the city's restaurants for resolution.

    A city-sized list is small enough to resolve in memory and makes the matcher
    deterministic and testable. If a city ever outgrows this, the trigram + GIST
    indexes are already in place to switch to a bounded SQL prefilter.
    """
    rows = session.execute(select(Restaurant).where(Restaurant.city_id == city_id)).scalars().all()

    candidates: list[CandidateRestaurant] = []
    for row in rows:
        aliases = tuple(a.normalized_alias for a in row.aliases)
        keys = tuple((s.source.value, s.external_id) for s in row.sources)
        candidates.append(
            CandidateRestaurant(
                id=str(row.id),
                name=row.name,
                normalized_name=row.normalized_name,
                lat=float(row.lat),
                lng=float(row.lng),
                aliases=aliases,
                source_keys=keys,
            )
        )
    return candidates


def _unique_slug(session: Session, city_id: uuid.UUID, name: str) -> str:
    base = slugify(name)
    slug = base
    suffix = 2
    while session.execute(
        select(Restaurant.id).where(Restaurant.city_id == city_id, Restaurant.slug == slug)
    ).first():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def upsert_place(
    session: Session,
    city: City,
    place: RawPlace,
    *,
    candidates: list[CandidateRestaurant] | None = None,
) -> tuple[Restaurant | None, str]:
    """Insert or update one place. Returns (restaurant, action).

    action ∈ {created, updated, skipped, conflict}. A `conflict` result means the
    resolver refused to choose and an `entity_conflicts` row was written — this is
    intentional; a wrong merge is far more expensive than an admin decision.
    """
    candidates = candidates if candidates is not None else load_candidates(session, city.id)

    payload_hash = content_hash(
        place.name, place.lat, place.lng, place.address, ",".join(sorted(place.cuisines))
    )

    existing_source = session.execute(
        select(RestaurantSource).where(
            RestaurantSource.source == place.source,
            RestaurantSource.external_id == place.external_id,
        )
    ).scalar_one_or_none()

    if existing_source is not None:
        if existing_source.content_hash == payload_hash:
            return existing_source.restaurant, "skipped"

        restaurant = existing_source.restaurant
        restaurant.name = place.name
        restaurant.normalized_name = normalize_name(place.name)
        restaurant.lat, restaurant.lng = place.lat, place.lng
        restaurant.location = f"SRID=4326;POINT({place.lng} {place.lat})"
        restaurant.address = place.address or restaurant.address
        restaurant.area = place.area or restaurant.area
        restaurant.cuisines = place.cuisines or restaurant.cuisines
        restaurant.price_level = place.price_level or restaurant.price_level
        restaurant.phone = place.phone or restaurant.phone
        restaurant.website = place.website or restaurant.website
        restaurant.opening_hours = place.opening_hours or restaurant.opening_hours
        restaurant.last_ingested_at = datetime.now(UTC)

        existing_source.content_hash = payload_hash
        existing_source.raw = place.raw
        existing_source.fetched_at = datetime.now(UTC)
        return restaurant, "updated"

    resolution = resolve_candidate(
        IncomingPlace(
            name=place.name,
            lat=place.lat,
            lng=place.lng,
            source=place.source.value,
            external_id=place.external_id,
        ),
        candidates,
    )

    if resolution.method is MatchMethod.AMBIGUOUS:
        session.add(
            EntityConflict(
                kind=ConflictKind.RESTAURANT,
                city_id=city.id,
                candidate_a=uuid.UUID(resolution.matched_id) if resolution.matched_id else None,
                candidate_b=uuid.UUID(resolution.runner_up_id) if resolution.runner_up_id else None,
                similarity=resolution.similarity,
                payload={
                    "incoming": {
                        "name": place.name,
                        "lat": place.lat,
                        "lng": place.lng,
                        "source": place.source.value,
                        "external_id": place.external_id,
                    },
                    "reason": "ambiguous_match",
                },
            )
        )
        log.info("entity_conflict_recorded", name=place.name, city=city.slug)
        return None, "conflict"

    if resolution.matched_id:
        restaurant = session.get(Restaurant, uuid.UUID(resolution.matched_id))
        action = "updated"
        if restaurant is not None:
            restaurant.last_ingested_at = datetime.now(UTC)
            restaurant.data_confidence = max(
                float(restaurant.data_confidence), resolution.confidence
            )
            if normalize_name(place.name) != restaurant.normalized_name:
                _add_alias(session, restaurant, place.name, place.source)
    else:
        restaurant = Restaurant(
            city_id=city.id,
            name=place.name,
            slug=_unique_slug(session, city.id, place.name),
            normalized_name=normalize_name(place.name),
            location=f"SRID=4326;POINT({place.lng} {place.lat})",
            lat=place.lat,
            lng=place.lng,
            address=place.address,
            area=place.area,
            cuisines=place.cuisines,
            price_level=place.price_level,
            phone=place.phone,
            website=place.website,
            opening_hours=place.opening_hours,
            osm_type=place.raw.get("osm_type") if place.source is SourceType.OSM else None,
            osm_id=place.raw.get("osm_id") if place.source is SourceType.OSM else None,
            data_confidence=resolution.confidence,
            first_seen_at=datetime.now(UTC),
            last_ingested_at=datetime.now(UTC),
        )
        session.add(restaurant)
        session.flush()
        action = "created"

    if restaurant is None:
        return None, "skipped"

    session.add(
        RestaurantSource(
            restaurant_id=restaurant.id,
            source=place.source,
            external_id=place.external_id,
            url=place.url,
            raw=place.raw,
            content_hash=payload_hash,
            license=place.license,
            attribution=place.attribution,
            fetched_at=datetime.now(UTC),
        )
    )
    return restaurant, action


def _add_alias(session: Session, restaurant: Restaurant, name: str, source: SourceType) -> None:
    normalized = normalize_name(name)
    if not normalized:
        return
    exists = session.execute(
        select(RestaurantAlias.id).where(
            RestaurantAlias.restaurant_id == restaurant.id,
            RestaurantAlias.normalized_alias == normalized,
        )
    ).first()
    if exists:
        return
    session.add(
        RestaurantAlias(
            restaurant_id=restaurant.id,
            alias=name,
            normalized_alias=normalized,
            source=source,
            confidence=0.7,
        )
    )


def store_review(
    session: Session,
    city: City,
    raw: RawReview,
    *,
    restaurant: Restaurant | None = None,
    candidates: list[CandidateRestaurant] | None = None,
) -> tuple[Review | None, str]:
    """Store one review idempotently. Returns (review, action).

    Layers, in order: source identity → exact content hash → near-duplicate flag.
    Only genuinely new evidence reaches `ai_state='pending'`.
    """
    body = clean_text(raw.text)
    if not body:
        return None, "skipped"

    existing_source = session.execute(
        select(ReviewSource).where(
            ReviewSource.source == raw.source, ReviewSource.external_id == raw.external_id
        )
    ).scalar_one_or_none()
    if existing_source is not None:
        return existing_source.review, "skipped"

    fingerprint = review_fingerprint(body, raw.author, raw.published_at)
    duplicate = session.execute(
        select(Review).where(Review.content_hash == fingerprint)
    ).scalar_one_or_none()
    if duplicate is not None:
        return duplicate, "skipped"

    target = restaurant
    if target is None:
        target = _resolve_review_restaurant(session, city, raw, candidates)
    if target is None:
        return None, "skipped"

    review = Review(
        restaurant_id=target.id,
        city_id=city.id,
        source=raw.source,
        title=raw.title,
        body=body,
        rating=raw.rating,
        rating_scale=raw.rating_scale,
        author_external=raw.author,
        engagement_score=raw.engagement,
        source_quality=source_quality_for(raw.source.value),
        published_at=raw.published_at,
        ingested_at=datetime.now(UTC),
        content_hash=fingerprint,
        simhash=to_signed_64(simhash(body)),
        # Ingested third-party text is published immediately (it is already public);
        # user submissions go through moderation in the API layer instead.
        status=ReviewStatus.PUBLISHED,
        ai_state=AIState.PENDING,
    )
    session.add(review)
    session.flush()

    session.add(
        ReviewSource(
            review_id=review.id,
            source=raw.source,
            external_id=raw.external_id,
            url=raw.url,
            permalink=raw.permalink,
            raw=raw.raw,
            license=raw.license,
            attribution=raw.attribution,
        )
    )

    target.review_count = (target.review_count or 0) + 1
    return review, "created"


def _resolve_review_restaurant(
    session: Session,
    city: City,
    raw: RawReview,
    candidates: list[CandidateRestaurant] | None,
) -> Restaurant | None:
    """A text-only source names a place; resolve it or drop the item.

    Dropping is correct: evidence attached to the wrong restaurant is worse than no
    evidence, and unresolved names would silently distort rankings.
    """
    if not raw.restaurant_hint:
        return None

    candidates = candidates if candidates is not None else load_candidates(session, city.id)
    resolution = resolve_candidate(
        IncomingPlace(
            name=raw.restaurant_hint,
            lat=raw.lat,
            lng=raw.lng,
            source=raw.source.value,
            external_id=None,
        ),
        candidates,
    )

    if resolution.matched_id and resolution.confidence >= 0.9:
        return session.get(Restaurant, uuid.UUID(resolution.matched_id))

    if resolution.needs_review:
        session.add(
            EntityConflict(
                kind=ConflictKind.RESTAURANT,
                city_id=city.id,
                candidate_a=uuid.UUID(resolution.matched_id) if resolution.matched_id else None,
                candidate_b=uuid.UUID(resolution.runner_up_id) if resolution.runner_up_id else None,
                similarity=resolution.similarity,
                payload={
                    "incoming": {"name": raw.restaurant_hint, "source": raw.source.value},
                    "reason": "unresolved_review_mention",
                },
            )
        )
    return None

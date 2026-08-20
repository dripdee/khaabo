"""Enrich the restaurant catalog from Wikidata (CC0; no API key required).

Reads free, structured data one hop away from our content goal:

* **aliases** for restaurants already in the catalog — so text-only evidence
  (YouTube/Reddit mentions that spell the name differently, e.g. "Arsalan
  Park Circus" vs "Arsalan") can land when it already can
* **new restaurants** (opt-in, `--create`) — places Wikidata knows about that
  OSM missed entirely

Idempotent like `scripts.seed`: safe to re-run. Alias rows are deduped on
(restaurant, normalized_alias), matching the UNIQUE constraint.

One-shot use (from backend/ inside the api container):
    python -m scripts.enrich_aliases                      # kolkata, aliases only
    python -m scripts.enrich_aliases --city kolkata --create

Scheduled: beat runs it every Monday at 03:00 UTC as `ingestion.enrich_aliases`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import sync_session
from app.ingestion.pipeline import _unique_slug
from app.models import City, Restaurant, RestaurantAlias, RestaurantSource
from app.models.enums import SourceType
from app.services.entity_resolution import (
    CandidateRestaurant,
    IncomingPlace,
    resolve_candidate,
)

# Reused so data seeding and mention-matching share one generic-token policy.
from app.services.mention_extraction import _is_mentionable as _mentionable
from app.utils.text import normalize_name

configure_logging()
log = get_logger(__name__)

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_LICENSE = "CC0"
WIKIDATA_ATTRIBUTION = "Wikidata"
EXTERNAL_ID_PREFIX = "wikidata:"

# Kitchen-scale classes: restaurant, cafe, fast food, coffeehouse, tea house.
FOOD_CLASSES = ("wd:Q11707", "wd:Q12444960", "wd:Q1751429", "wd:Q30022", "wd:Q30023")

# Administrative-area entity per city (`wdt:P131` containment). The geo-spatial
# `wikibase:around` service errors out on the public endpoint, so we match by
# "located in" instead; Wikidata coverage does not include coordinates for most
# rows anyway, so `--create` skips anything without them.
CITY_WIKIDATA_QIDS = {"kolkata": "Q1348"}

# Alias confidence: Wikidata labels are clean, but altLabels can hold vestigial
# transcriptions — deliberately below OSM's 0.7 default and far below a source
# key. Matching strength is limited downstream anyway (0.9 bar).
ALIAS_CONFIDENCE = 0.65

_SPARQL_TEMPLATE = """
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?item ?label ?alias WHERE {{
  ?item wdt:P31 ?class .
  VALUES ?class {{ {classes} }}
  ?item wdt:P131 wd:{qid} .
  ?item rdfs:label ?label FILTER(LANG(?label) = "en") .
  OPTIONAL {{ ?item skos:altLabel ?alias FILTER(LANG(?alias) = "en") . }}
}}
"""


@dataclass(slots=True)
class WikidataPlace:
    qid: str
    label: str
    aliases: set[str] = field(default_factory=set)
    lat: float | None = None
    lng: float | None = None


async def fetch_wikidata_places(entity_qid: str) -> list[WikidataPlace]:
    """One SPARQL query per city; Wikidata coverage is small enough to fit."""
    query = _SPARQL_TEMPLATE.format(classes=" ".join(FOOD_CLASSES), qid=entity_qid)
    headers = {"User-Agent": settings.user_agent, "Accept": "application/sparql-results+json"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(WIKIDATA_ENDPOINT, params={"query": query}, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        log.warning("wikidata_unavailable", error=str(exc))
        return []

    places: dict[str, WikidataPlace] = {}
    for row in payload.get("results", {}).get("bindings", []):
        qid = row.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        label = row.get("label", {}).get("value", "").strip()
        if not qid or not label:
            continue
        place = places.setdefault(qid, WikidataPlace(qid=qid, label=label))

        alias = row.get("alias", {}).get("value", "").strip()
        if alias and alias != label:
            place.aliases.add(alias)

        if place.lat is None and row.get("lat") and row.get("lng"):
            try:
                place.lat = float(row["lat"]["value"])
                place.lng = float(row["lng"]["value"])
            except (KeyError, TypeError, ValueError):
                continue

    return list(places.values())


def load_city_candidates(
    session: Session, city_id
) -> tuple[list[CandidateRestaurant], dict[str, Restaurant]]:
    rows = session.execute(select(Restaurant).where(Restaurant.city_id == city_id)).scalars().all()
    candidates = [
        CandidateRestaurant(
            id=str(r.id),
            name=r.name,
            normalized_name=r.normalized_name,
            lat=float(r.lat),
            lng=float(r.lng),
        )
        for r in rows
    ]
    return candidates, {str(r.id): r for r in rows}


def _attach_aliases(session, restaurant, names: set[str]) -> int:
    added = 0
    existing = {a.normalized_alias for a in restaurant.aliases}
    existing.add(restaurant.normalized_name)

    for name in sorted(names):
        normalized = normalize_name(name)
        if not normalized or not _mentionable(normalized):
            continue
        if normalized in existing:
            continue
        found = (
            session.query(RestaurantAlias.id)
            .filter_by(restaurant_id=restaurant.id, normalized_alias=normalized)
            .first()
        )
        if found is not None:
            continue
        session.add(
            RestaurantAlias(
                restaurant_id=restaurant.id,
                alias=name,
                normalized_alias=normalized,
                source=SourceType.MANUAL,
                confidence=ALIAS_CONFIDENCE,
            )
        )
        added += 1
    return added


def _create_restaurant(session, city: City, place: WikidataPlace) -> Restaurant | None:
    if place.lat is None or place.lng is None:
        return None
    restaurant = Restaurant(
        city_id=city.id,
        name=place.label,
        slug=_unique_slug(session, city.id, place.label),
        normalized_name=normalize_name(place.label),
        location=f"SRID=4326;POINT({place.lng} {place.lat})",
        lat=place.lat,
        lng=place.lng,
        data_confidence=0.5,
        first_seen_at=datetime.now(UTC),
        last_ingested_at=datetime.now(UTC),
    )
    session.add(restaurant)
    session.flush()

    session.add(
        RestaurantSource(
            restaurant_id=restaurant.id,
            source=SourceType.MANUAL,  # curator knowledge, not a live trace
            external_id=f"{EXTERNAL_ID_PREFIX}{place.qid}",
            url=f"https://www.wikidata.org/wiki/{place.qid}",
            raw={"label": place.label, "aliases": sorted(place.aliases)},
            content_hash=f"wikidata:{place.qid}",
            license=WIKIDATA_LICENSE,
            attribution=WIKIDATA_ATTRIBUTION,
            fetched_at=datetime.now(UTC),
        )
    )
    return restaurant


def enrich_city_sync(session: Session, city: City, places: list[WikidataPlace], *, create: bool):
    """Attach fetched places to the catalog inside one transaction.

    Returns a stats dict. Dedupe is structural: matched-by-name restaurants are
    skipped when they already carry a `wikidata:<Q-id>` source row, and the alias
    attachment itself is unique-constrained on (restaurant, normalized_alias).
    """
    stats = {"places": len(places), "aliases_added": 0, "created": 0, "skipped": 0}
    if not places:
        return stats

    candidates, by_id = load_city_candidates(session, city.id)
    already_seeded = {
        s.external_id
        for s in session.execute(
            select(RestaurantSource.external_id).where(
                RestaurantSource.source == SourceType.MANUAL,
                RestaurantSource.external_id.like(f"{EXTERNAL_ID_PREFIX}%"),
            )
        ).scalars()
    }

    for place in places:
        resolution = resolve_candidate(
            IncomingPlace(name=place.label, lat=place.lat, lng=place.lng),
            candidates,
        )

        matched: Restaurant | None = None
        if resolution.matched_id and not resolution.is_ambiguous:
            matched = by_id.get(resolution.matched_id)

        if matched is None:
            if create:
                # Creation writes its own RestaurantSource row for dedupe.
                matched = _create_restaurant(session, city, place)
                if matched is None:
                    stats["skipped"] += 1
                    continue
                stats["created"] += 1
            else:
                stats["skipped"] += 1
                continue
        elif f"{EXTERNAL_ID_PREFIX}{place.qid}" in already_seeded:
            stats["skipped"] += 1
            continue
        else:
            # Record provenance so a re-run skips this place instead of
            # re-scanning it.
            session.add(
                RestaurantSource(
                    restaurant_id=matched.id,
                    source=SourceType.MANUAL,
                    external_id=f"{EXTERNAL_ID_PREFIX}{place.qid}",
                    url=f"https://www.wikidata.org/wiki/{place.qid}",
                    raw={"label": place.label, "aliases": sorted(place.aliases)},
                    content_hash=f"wikidata:{place.qid}",
                    license=WIKIDATA_LICENSE,
                    attribution=WIKIDATA_ATTRIBUTION,
                    fetched_at=datetime.now(UTC),
                )
            )

        names = {place.label, *place.aliases}
        stats["aliases_added"] += _attach_aliases(session, matched, names)

    return stats


def enrich_city(city: City, *, create: bool) -> dict:
    qid = CITY_WIKIDATA_QIDS.get(city.slug)
    zeros = {"places": 0, "aliases_added": 0, "created": 0, "skipped": 0}
    if qid is None:
        log.warning("wikidata_city_unknown", city=city.slug)
        return zeros

    places = asyncio.run(fetch_wikidata_places(qid))
    log.info("wikidata_fetched", city=city.slug, count=len(places))

    if not places:
        return zeros

    with sync_session() as session:
        return enrich_city_sync(session, city, places, create=create)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich restaurant catalog from Wikidata")
    parser.add_argument("--city", default=settings.default_city_slug)
    parser.add_argument(
        "--create",
        action="store_true",
        help="also create restaurants missing from the catalog",
    )
    args = parser.parse_args()

    with sync_session() as session:
        city = session.execute(select(City).where(City.slug == args.city)).scalars().first()

        if city is None:
            print(f"city '{args.city}' not found; run scripts.seed first", file=sys.stderr)
            return 1

        stats = enrich_city(city, create=args.create)

    print(
        f"wikidata places={stats['places']} aliases_added={stats['aliases_added']} "
        f"created={stats['created']} skipped={stats['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""OpenStreetMap adapters: Overpass (places) and Nominatim (geocoding).

Compliance notes (these are requirements, not preferences):
* identifiable User-Agent with contact info — from `settings.user_agent`
* Nominatim: max 1 request/second, results cached aggressively
* Overpass: one bbox query per run, ≥2 s between calls, mirror rotation on failure
* attribution "© OpenStreetMap contributors" is stored per record and rendered in UI
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.errors import PermanentSourceError, TransientSourceError
from app.core.logging import get_logger
from app.ingestion.base import CityRef, RateLimiter, RawPlace, SourceAdapter, request_json
from app.models.enums import SourceType

log = get_logger(__name__)

OSM_LICENSE = "ODbL-1.0"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

FOOD_AMENITIES = ("restaurant", "cafe", "fast_food", "food_court", "ice_cream")

# OSM price tags are inconsistent; map the ones that actually appear.
_PRICE_HINTS = {
    "cheap": 1,
    "budget": 1,
    "moderate": 2,
    "mid-range": 2,
    "expensive": 3,
    "luxury": 4,
    "very_expensive": 4,
}

_overpass_limiter = RateLimiter(settings.overpass_min_interval_seconds)
_nominatim_limiter = RateLimiter(settings.nominatim_min_interval_seconds)


def build_overpass_query(city: CityRef) -> str:
    """Single around-query covering nodes and ways, returning centroids."""
    amenity = "|".join(FOOD_AMENITIES)
    return f"""
[out:json][timeout:90];
(
  node["amenity"~"{amenity}"](around:{city.radius_m},{city.lat},{city.lng});
  way["amenity"~"{amenity}"](around:{city.radius_m},{city.lat},{city.lng});
);
out center tags;
""".strip()


class OverpassAdapter(SourceAdapter):
    source = SourceType.OSM

    def __init__(self) -> None:
        self.default_interval_hours = settings.source_interval_osm_hours
        self.endpoints = settings.overpass_endpoints

    async def discover_places(self, city: CityRef) -> list[RawPlace]:
        query = build_overpass_query(city)
        payload: dict | None = None
        last_error: Exception | None = None

        # Mirror rotation: public Overpass instances are frequently overloaded, and
        # rotating is explicitly the recommended client behaviour.
        async with httpx.AsyncClient(timeout=120.0) as client:
            for endpoint in self.endpoints:
                try:
                    payload = await request_json(
                        client,
                        "POST",
                        endpoint,
                        limiter=_overpass_limiter,
                        content=query.encode("utf-8"),
                        headers={"Content-Type": "text/plain; charset=utf-8"},
                    )
                    break
                except (TransientSourceError, PermanentSourceError) as exc:
                    last_error = exc
                    log.warning("overpass_endpoint_failed", endpoint=endpoint, error=str(exc))
                    continue

        if payload is None:
            raise TransientSourceError(
                "All Overpass endpoints failed",
                details={"last_error": str(last_error)[:200]},
            )

        places: list[RawPlace] = []
        for element in payload.get("elements", []):
            place = self._to_place(element)
            if place is not None:
                places.append(place)

        log.info("overpass_discovered", city=city.slug, count=len(places))
        return places

    def _to_place(self, element: dict) -> RawPlace | None:
        tags = element.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            return None  # unnamed nodes are useless for a discovery product

        if element.get("type") == "node":
            lat, lng = element.get("lat"), element.get("lon")
        else:
            center = element.get("center") or {}
            lat, lng = center.get("lat"), center.get("lon")

        if lat is None or lng is None:
            return None

        cuisines = [
            c.strip().replace("_", " ") for c in (tags.get("cuisine") or "").split(";") if c.strip()
        ]
        if tags.get("amenity") == "cafe" and "cafe" not in cuisines:
            cuisines.append("cafe")

        osm_type = element.get("type", "node")
        osm_id = element.get("id")

        # OSM lets values repeat via `;` ("+91-…;+91-…"). The catalog columns
        # are fixed-width; keep the first entry and clip to the column size so
        # a chatty tag can never abort the whole city ingest.
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        phone = phone.split(";")[0].strip()[:40] or None
        area = tags.get("addr:suburb") or tags.get("addr:neighbourhood") or ""
        area = area.strip()[:160] or None

        return RawPlace(
            source=SourceType.OSM,
            external_id=f"{osm_type}/{osm_id}",
            name=name,
            lat=float(lat),
            lng=float(lng),
            address=_compose_address(tags),
            area=area,
            cuisines=cuisines,
            price_level=_PRICE_HINTS.get((tags.get("price_range") or "").lower()),
            phone=phone,
            website=tags.get("website") or tags.get("contact:website"),
            opening_hours=tags.get("opening_hours"),
            url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            license=OSM_LICENSE,
            attribution=OSM_ATTRIBUTION,
            raw={"tags": tags, "osm_type": osm_type, "osm_id": osm_id},
        )

    async def health(self) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for endpoint in self.endpoints:
                try:
                    resp = await client.get(endpoint.replace("/interpreter", "/status"))
                    if resp.status_code < 500:
                        return True
                except httpx.HTTPError:
                    continue
        return False


def _compose_address(tags: dict) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or tags.get("addr:full")


class NominatimAdapter(SourceAdapter):
    """Geocoding only. Results are cached by callers; 1 req/s is enforced here."""

    source = SourceType.OSM

    def __init__(self) -> None:
        self.base_url = settings.nominatim_url.rstrip("/")

    async def geocode(self, query: str, *, city: str | None = None) -> dict | None:
        params: dict[str, object] = {
            "q": f"{query}, {city}" if city else query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            data = await request_json(
                client, "GET", f"{self.base_url}/search", limiter=_nominatim_limiter, params=params
            )
        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return None
        first = results[0]
        return {
            "lat": float(first["lat"]),
            "lng": float(first["lon"]),
            "display_name": first.get("display_name"),
            "type": first.get("type"),
            "address": first.get("address", {}),
            "license": OSM_LICENSE,
            "attribution": OSM_ATTRIBUTION,
        }

    async def reverse(self, lat: float, lng: float) -> dict | None:
        params = {"lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1, "zoom": 16}
        async with httpx.AsyncClient(timeout=20.0) as client:
            data = await request_json(
                client, "GET", f"{self.base_url}/reverse", limiter=_nominatim_limiter, params=params
            )
        if not data or "address" not in data:
            return None
        address = data["address"]
        return {
            "area": address.get("suburb")
            or address.get("neighbourhood")
            or address.get("city_district"),
            "city": address.get("city") or address.get("town") or address.get("state_district"),
            "display_name": data.get("display_name"),
            "license": OSM_LICENSE,
            "attribution": OSM_ATTRIBUTION,
        }

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{self.base_url}/status", headers={"User-Agent": settings.user_agent}
                )
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

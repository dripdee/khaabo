"""Google Places (New) Nearby Search adapter with a hard monthly quota guard.

Pricing dictates the design (India billing profile, verified Aug 2026):
* The field mask includes `rating`/`userRatingCount`, so every call bills the
  Nearby Search **Pro** SKU: 35,000 requests/month free, then $9.60/1000.
* Any Atmosphere field (reviews, editorialSummary, photos) would jump the bill
  to Enterprise SKUs — the field mask below is therefore frozen by policy.
* The guard atomically reserves 1 unit in Redis before every call, refunds on
  unbilled failures (network error, 429), and stops the run when the monthly
  budget would be crossed. A separate per-run cap bounds the worst case even
  without Redis.
* ToS content caching is ~30 days: aggregate rating + count only, never review
  text; beat refreshes monthly.

Only the official `places:searchNearby` endpoint is used; no scraping.
"""

from __future__ import annotations

import math
from contextlib import suppress
from datetime import UTC, datetime

import httpx

from app.core.cache import get_redis
from app.core.config import settings
from app.core.errors import PermanentSourceError, TransientSourceError
from app.core.logging import get_logger
from app.ingestion.base import CityRef, RateLimiter, RawPlace, SourceAdapter, request_json
from app.models.enums import SourceType

log = get_logger(__name__)

API_BASE = "https://places.googleapis.com/v1"
GOOGLE_LICENSE = "google-maps-platform-tos"
GOOGLE_ATTRIBUTION = "Google"

# Pro-tier fields only. Frozen by policy: adding reviews/editorialSummary/photos
# or any Atmosphere field moves the bill to Enterprise SKUs ($6-12/1000).
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.rating,places.userRatingCount,places.types,places.priceLevel,"
    "places.websiteUri"
)
_FORBIDDEN_FIELDS = ("reviews", "editorialSummary", "photos", "atmosphere")

PLACES_TYPES = (
    "restaurant",
    "cafe",
    "food_court",
    "bakery",
    "ice_cream_shop",
    "meal_takeaway",
)

MAX_RESULTS_PER_PAGE = 20  # API maximum for searchNearby (`maxResultCount` in REST)
MAX_PAGES_PER_CELL = 3  # Places API caps token pagination at 3 pages

# ~3 km grid over a 25 km radius circle yields ~250-550 cells (legacy geometry,
# kept for grid_centers callers; the live sweep uses sweep_cells below).
CELL_M = 2500.0
_MAX_API_RADIUS_M = 50000

# Contract test: if the frozen mask ever gains Enterprise fields, this trips.
assert not any(f in FIELD_MASK for f in _FORBIDDEN_FIELDS)

# Politeness between calls, mirroring the per-host limiters used elsewhere.
_places_limiter = RateLimiter(0.1)

_METERS_PER_DEG_LAT = 111_320.0

_PRICE_LEVELS = {
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


def haversine_m(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """Great-circle distance in metres (good enough for grid bookkeeping)."""
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lng = math.radians(lng_b - lng_a)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lng / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(h))


def grid_centers(
    lat: float,
    lng: float,
    radius_m: int,
    cell_m: float = CELL_M,
    *,
    offset_fraction: float = 0.0,
) -> list[tuple[float, float]]:
    """Cell centres whose distance from the city centre stays within radius_m.

    Only cells inside the circle are searched, so the call count is bounded by
    `len(cells) * MAX_PAGES_PER_CELL` and nothing outside the city is queried.

    `offset_fraction` shifts the whole lattice by that fraction of a cell
    (0.5 = half a cell) so the twice-monthly offset sweep captures places that
    sat on cell seams during the base sweep.
    """
    n = max(1, int(radius_m // cell_m))
    m_per_deg_lng = _METERS_PER_DEG_LAT * math.cos(math.radians(lat))
    shift = offset_fraction * cell_m
    centers: list[tuple[float, float]] = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            cell_lat = lat + (i * cell_m + shift) / _METERS_PER_DEG_LAT
            cell_lng = lng + (j * cell_m + shift) / m_per_deg_lng
            if haversine_m(lat, lng, cell_lat, cell_lng) <= radius_m:
                centers.append((cell_lat, cell_lng))
    return centers


def sweep_cells(
    lat: float, lng: float, radius_m: int, *, offset: bool = False
) -> list[tuple[float, float, float]]:
    """Two-zone sweep geometry: `(lat, lng, search_radius_m)` per cell.

    Dense cores saturate Google's 60-results-per-cell cap inside 3 km cells,
    so the inner `google_places_fine_radius_m` is covered with fine cells and
    the remaining ring with coarse ones. Kolkata defaults (300 m inside 10 km,
    1 km out to 25 km): ~3,490 + ~1,650 ≈ 5,100-5,600 cells ≈ ~6,000 requests
    per sweep — twice monthly stays well inside the 30,000 guard budget.

    Search radius per cell equals the cell size: the exact covering radius is
    cell/sqrt(2), so the extra overlap only adds place_id duplicates, which
    the sweep already dedupes.

    `offset=True` shifts every lattice by half a cell (150 m fine / 500 m
    coarse) for the day-15 seam sweep.
    """
    fine_cell = float(settings.google_places_fine_cell_m)
    fine_radius = min(radius_m, settings.google_places_fine_radius_m)
    coarse_cell = float(settings.google_places_coarse_cell_m)
    fraction = 0.5 if offset else 0.0

    cells = [
        (cell_lat, cell_lng, fine_cell)
        for cell_lat, cell_lng in grid_centers(
            lat, lng, fine_radius, fine_cell, offset_fraction=fraction
        )
    ]
    if radius_m > fine_radius:
        cells.extend(
            (cell_lat, cell_lng, coarse_cell)
            for cell_lat, cell_lng in grid_centers(
                lat, lng, radius_m, coarse_cell, offset_fraction=fraction
            )
            if haversine_m(lat, lng, cell_lat, cell_lng) > fine_radius
        )
    return cells


class PlacesQuotaGuard:
    """Monthly request budget, tracked in Redis so it survives worker restarts.

    `reserve()` is atomic (INCRBY, then roll back if the budget would be
    crossed), so concurrent beat ticks or retries can never overshoot the cap.
    The key is monthly (`gplaces:calls:YYYY-MM`) with a 45-day TTL.

    Without Redis the run is still allowed: the per-run request cap bounds the
    spend, and refusing to ingest at all would be worse.
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget

    def _key(self) -> str:
        return f"gplaces:calls:{datetime.now(UTC):%Y-%m}"

    async def spent(self) -> int:
        client = get_redis()
        if client is None:
            return 0
        try:
            value = await client.get(self._key())
            return int(value or 0)
        except Exception:
            return 0

    async def reserve(self, cost: int) -> bool:
        """Atomically reserve `cost` units against the monthly budget.

        Returns False (having reserved nothing) if the reservation would push
        the month's spend past budget. Callers must never issue the API call
        when this returns False.
        """
        client = get_redis()
        if client is None:
            return True
        try:
            new_total = await client.incrby(self._key(), cost)
            await client.expire(self._key(), 45 * 86400, nx=True)
            if new_total > self.budget:
                # Over budget: take the reservation back and refuse the work.
                await client.incrby(self._key(), -cost)
                return False
            return True
        except Exception:
            # Redis hiccup: allow the run — per-run caps still bound the spend.
            return True

    async def refund(self, cost: int) -> None:
        """Return reserved units for a call Google did not bill (network error, 429)."""
        client = get_redis()
        if client is None:
            return
        with suppress(Exception):
            await client.incrby(self._key(), -cost)


class GooglePlacesAdapter(SourceAdapter):
    source = SourceType.GOOGLE

    def __init__(self) -> None:
        self.default_interval_hours = settings.google_refresh_interval_hours
        self.quota = PlacesQuotaGuard(settings.google_places_monthly_limit)

    @property
    def configured(self) -> bool:
        return bool(settings.google_places_api_key)

    async def discover_places(self, city: CityRef, *, offset: bool = False) -> list[RawPlace]:
        if not self.configured:
            log.info("google_places_skipped_not_configured")
            return []

        places: list[RawPlace] = []
        seen: set[str] = set()
        requests_made = 0
        stopped_early = False

        async with httpx.AsyncClient(timeout=30.0, base_url=API_BASE) as client:
            for cell_lat, cell_lng, cell_radius in sweep_cells(
                city.lat, city.lng, city.radius_m, offset=offset
            ):
                if stopped_early:
                    break

                page_token: str | None = None
                for _ in range(MAX_PAGES_PER_CELL):
                    if requests_made >= settings.google_places_max_requests_per_run:
                        log.info(
                            "google_places_run_cap_reached",
                            city=city.slug,
                            requests=requests_made,
                        )
                        stopped_early = True
                        break

                    # Reserve one unit before the call; never call without a held
                    # reservation. An exhausted budget stops the run with what it has.
                    if not await self.quota.reserve(1):
                        log.info(
                            "places_quota_exhausted",
                            city=city.slug,
                            spent=await self.quota.spent(),
                            places=len(places),
                        )
                        stopped_early = True
                        break

                    log.info("places_quota_reserved", reserved=1, run_total=requests_made + 1)
                    try:
                        payload = await self._search_cell(
                            client, cell_lat, cell_lng, cell_radius, page_token
                        )
                    except TransientSourceError:
                        # Network error / 429 — Google does not bill these: refund.
                        await self.quota.refund(1)
                        raise
                    except PermanentSourceError as exc:
                        # The call was issued (and billed): it counts toward the
                        # run cap, but is NOT refunded. 401/403 are credential
                        # failures that poison every further call; any other 4xx
                        # is per-cell and non-fatal.
                        requests_made += 1
                        status = exc.details.get("status")
                        if status in {401, 403}:
                            raise
                        log.warning(
                            "google_places_cell_skipped",
                            cell_lat=round(cell_lat, 4),
                            cell_lng=round(cell_lng, 4),
                            status=status,
                            error=exc.message[:200],
                        )
                        break

                    requests_made += 1

                    for item in payload.get("places", []):
                        place = self._to_place(item)
                        if place is None or place.external_id in seen:
                            continue
                        seen.add(place.external_id)
                        places.append(place)

                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        break

        log.info(
            "google_places_discovered",
            city=city.slug,
            count=len(places),
            requests=requests_made,
            stopped_early=stopped_early,
        )
        return places

    async def _search_cell(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lng: float,
        radius_m: float,
        page_token: str | None,
    ) -> dict:
        body: dict = {
            "includedTypes": list(PLACES_TYPES),
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": min(radius_m, _MAX_API_RADIUS_M),
                }
            },
            "maxResultCount": MAX_RESULTS_PER_PAGE,
        }
        if page_token:
            body["pageToken"] = page_token

        return await request_json(
            client,
            "POST",
            "/places:searchNearby",
            limiter=_places_limiter,
            json=body,
            headers={
                "X-Goog-Api-Key": settings.google_places_api_key,
                "X-Goog-FieldMask": FIELD_MASK,
                "Content-Type": "application/json",
            },
        )

    def _to_place(self, item: dict) -> RawPlace | None:
        place_id = item.get("id")
        name = ((item.get("displayName") or {}).get("text") or "").strip()
        if not place_id or not name:
            return None

        if item.get("businessStatus") == "CLOSED_PERMANENTLY":
            return None

        location = item.get("location") or {}
        lat, lng = location.get("latitude"), location.get("longitude")
        if lat is None or lng is None:
            return None

        rating = item.get("rating")
        rating_count = item.get("userRatingCount")

        return RawPlace(
            source=SourceType.GOOGLE,
            external_id=place_id,
            name=name,
            lat=float(lat),
            lng=float(lng),
            address=item.get("formattedAddress"),
            rating=float(rating) if rating is not None else None,
            rating_count=int(rating_count) if rating_count is not None else None,
            price_level=_PRICE_LEVELS.get(item.get("priceLevel") or ""),
            website=item.get("websiteUri"),
            url=f"https://www.google.com/maps/place/?q=place_id:{place_id}",
            license=GOOGLE_LICENSE,
            attribution=GOOGLE_ATTRIBUTION,
            raw={"place_id": place_id, "types": item.get("types") or []},
        )

    async def health(self) -> bool:
        return self.configured

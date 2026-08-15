"""Entity resolution: map an incoming place/mention to a canonical restaurant.

Ordered, deterministic and *refusal-capable*. When two candidates are too close to
separate, the resolver declines and records an `entity_conflicts` row instead of
guessing — a wrong merge is much more expensive than an admin decision.

The scoring core is pure (`resolve_candidate`) so ambiguity behaviour is unit
testable without a database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from app.utils.text import normalize_name, token_set

EARTH_RADIUS_M = 6_371_000.0

EXACT_DISTANCE_M = 250.0  # same-name matches must be plausibly the same address
CHAIN_DISTANCE_M = 1_000.0  # a relocated/mis-geocoded branch
NAME_SIM_STRONG = 0.92
NAME_SIM_MODERATE = 0.82
AMBIGUITY_MARGIN = 0.05  # candidates within this margin are not separable


class MatchMethod(StrEnum):
    SOURCE_KEY = "source_key"
    EXACT_NAME = "exact_name"
    ALIAS = "alias"
    FUZZY_GEO = "fuzzy_geo"
    FUZZY_STRICT = "fuzzy_strict"
    NEW = "new"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class CandidateRestaurant:
    id: str
    name: str
    normalized_name: str
    lat: float
    lng: float
    aliases: tuple[str, ...] = ()
    source_keys: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class IncomingPlace:
    name: str
    lat: float | None = None
    lng: float | None = None
    source: str | None = None
    external_id: str | None = None


@dataclass(slots=True)
class ResolutionResult:
    matched_id: str | None
    method: MatchMethod
    confidence: float
    similarity: float = 0.0
    distance_m: float | None = None
    runner_up_id: str | None = None
    is_ambiguous: bool = False

    @property
    def should_create(self) -> bool:
        return self.method is MatchMethod.NEW

    @property
    def needs_review(self) -> bool:
        return self.is_ambiguous or self.method is MatchMethod.AMBIGUOUS


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def name_similarity(a: str, b: str) -> float:
    """Blend of sequence ratio and token overlap.

    Sequence ratio alone over-rewards shared prefixes ("Momo Mia" vs "Momo Mahal"),
    token overlap alone ignores order; the max of the two with a token-weighted
    floor behaves better on real restaurant names than either.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    seq = SequenceMatcher(None, na, nb).ratio()

    ta, tb = token_set(na), token_set(nb)
    overlap = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0

    # Containment: "wow momo" inside "wow momo salt lake" is a strong signal.
    containment = 0.0
    if ta and tb:
        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        containment = len(smaller & larger) / len(smaller)

    return round(max(seq, overlap, 0.85 * containment), 4)


def resolve_candidate(
    incoming: IncomingPlace,
    candidates: list[CandidateRestaurant],
) -> ResolutionResult:
    """Resolve `incoming` against `candidates` (already scoped to one city)."""
    # 1. provenance key — unambiguous by definition
    if incoming.source and incoming.external_id:
        key = (incoming.source, incoming.external_id)
        for cand in candidates:
            if key in cand.source_keys:
                return ResolutionResult(
                    matched_id=cand.id,
                    method=MatchMethod.SOURCE_KEY,
                    confidence=1.0,
                    similarity=1.0,
                    distance_m=_distance(incoming, cand),
                )

    incoming_norm = normalize_name(incoming.name)
    if not incoming_norm:
        return ResolutionResult(matched_id=None, method=MatchMethod.NEW, confidence=0.0)

    scored: list[tuple[float, MatchMethod, CandidateRestaurant, float, float | None]] = []

    for cand in candidates:
        distance = _distance(incoming, cand)
        similarity = name_similarity(incoming_norm, cand.normalized_name)

        alias_hit = any(normalize_name(a) == incoming_norm for a in cand.aliases)

        if cand.normalized_name == incoming_norm and _within(distance, EXACT_DISTANCE_M):
            scored.append((0.95, MatchMethod.EXACT_NAME, cand, 1.0, distance))
            continue

        if alias_hit and _within(distance, CHAIN_DISTANCE_M):
            scored.append((0.90, MatchMethod.ALIAS, cand, 1.0, distance))
            continue

        if similarity >= NAME_SIM_MODERATE and _within(distance, EXACT_DISTANCE_M):
            scored.append(
                (0.60 + 0.4 * similarity, MatchMethod.FUZZY_GEO, cand, similarity, distance)
            )
            continue

        if similarity >= NAME_SIM_STRONG and _within(distance, CHAIN_DISTANCE_M):
            scored.append(
                (0.55 + 0.3 * similarity, MatchMethod.FUZZY_STRICT, cand, similarity, distance)
            )
            continue

        # No coordinates at all (e.g. a Reddit mention): names must be near-identical.
        if distance is None and similarity >= NAME_SIM_STRONG:
            scored.append(
                (0.55 + 0.3 * similarity, MatchMethod.FUZZY_STRICT, cand, similarity, None)
            )

    if not scored:
        return ResolutionResult(matched_id=None, method=MatchMethod.NEW, confidence=0.30)

    scored.sort(key=lambda row: (-row[0], row[2].id))
    best_conf, best_method, best, best_sim, best_dist = scored[0]

    ambiguous = False
    runner_up_id = None
    if len(scored) > 1:
        second_conf, second_method, second, _, _ = scored[1]
        runner_up_id = second.id
        # Ambiguity is judged *within the same match tier*. Comparing across tiers
        # would flag a clean exact-name hit as ambiguous merely because a weaker
        # fuzzy candidate scored numerically close, which is not a real tie.
        ambiguous = (
            second_method is best_method and abs(best_conf - second_conf) <= AMBIGUITY_MARGIN
        )

    # Refuse to choose between two equally good candidates. For exact-name ties this
    # usually means our own table already holds duplicate rows, which is precisely
    # what the entity_conflicts queue exists to resolve.
    if ambiguous:
        return ResolutionResult(
            matched_id=None,
            method=MatchMethod.AMBIGUOUS,
            confidence=round(best_conf, 3),
            similarity=best_sim,
            distance_m=best_dist,
            runner_up_id=runner_up_id,
            is_ambiguous=True,
        )

    return ResolutionResult(
        matched_id=best.id,
        method=best_method,
        confidence=round(min(1.0, best_conf), 3),
        similarity=best_sim,
        distance_m=best_dist,
        runner_up_id=runner_up_id,
        is_ambiguous=ambiguous,
    )


def _distance(incoming: IncomingPlace, cand: CandidateRestaurant) -> float | None:
    if incoming.lat is None or incoming.lng is None:
        return None
    return haversine_m(incoming.lat, incoming.lng, cand.lat, cand.lng)


def _within(distance: float | None, limit: float) -> bool:
    """No coordinates means we cannot assert proximity, so the gate fails closed."""
    return distance is not None and distance <= limit

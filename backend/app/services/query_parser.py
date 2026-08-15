"""Natural-language search query parsing.

Turns "best chicken momo under 300 near salt lake" into structured intent before
any DB work. Deterministic and unit-testable; no model involved, so it cannot
hallucinate a filter the user did not ask for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.utils.text import normalize_name

# Kolkata areas seed the recognizer; other cities add their own via `known_areas`.
DEFAULT_AREAS = (
    "salt lake",
    "sector v",
    "sector 5",
    "park street",
    "new town",
    "rajarhat",
    "gariahat",
    "ballygunge",
    "behala",
    "howrah",
    "dumdum",
    "dum dum",
    "esplanade",
    "sealdah",
    "jadavpur",
    "tollygunge",
    "shyambazar",
    "bhowanipore",
    "alipore",
    "kasba",
    "santoshpur",
    "garia",
    "barasat",
    "chinar park",
    "lake town",
    "bidhannagar",
    "kankurgachi",
    "bowbazar",
    "burrabazar",
    "college street",
    "camac street",
    "elgin",
    "hatibagan",
    "ultadanga",
    "baguiati",
    "kestopur",
)

DIETARY_TERMS = {
    "veg": "veg",
    "vegetarian": "veg",
    "pure veg": "veg",
    "vegan": "vegan",
    "non veg": "non_veg",
    "nonveg": "non_veg",
    "non-veg": "non_veg",
    "halal": "halal",
    "jain": "jain",
    "eggetarian": "egg",
    "egg": "egg",
    "gluten free": "gluten_free",
}

MOOD_TERMS = {
    "working": "work",
    "work": "work",
    "wfh": "work",
    "laptop": "work",
    "studying": "study",
    "study": "study",
    "reading": "study",
    "date": "date",
    "romantic": "date",
    "anniversary": "date",
    "family": "family",
    "kids": "family",
    "late night": "late_night",
    "midnight": "late_night",
    "after party": "late_night",
    "breakfast": "breakfast",
    "brunch": "brunch",
    "quick bite": "quick",
    "takeaway": "takeaway",
    "delivery": "delivery",
    "group": "group",
    "party": "group",
    "birthday": "group",
    "solo": "solo",
    "alone": "solo",
}

CUISINE_TERMS = (
    "bengali",
    "chinese",
    "tibetan",
    "japanese",
    "korean",
    "thai",
    "italian",
    "mughlai",
    "north indian",
    "south indian",
    "continental",
    "mexican",
    "american",
    "lebanese",
    "awadhi",
    "hyderabadi",
    "nepali",
    "burmese",
    "vietnamese",
    "tandoori",
    "seafood",
    "bakery",
    "cafe",
)

_CHEAP = ("cheap", "budget", "affordable", "value for money", "inexpensive", "pocket friendly")
_PREMIUM = ("premium", "fine dining", "luxury", "upscale", "expensive")
_SUPERLATIVE = ("best", "top", "greatest", "finest", "must try", "must-try", "famous")
_NEAR_ME = ("near me", "nearby", "near by", "around me", "close to me", "closest")

_UNDER = re.compile(
    r"(?:under|below|less than|within|upto|up to|max)\s*(?:₹|rs\.?|inr)?\s*(\d{2,5})",
    re.IGNORECASE,
)
_OVER = re.compile(r"(?:over|above|more than)\s*(?:₹|rs\.?|inr)?\s*(\d{2,5})", re.IGNORECASE)
_BETWEEN = re.compile(
    r"(?:between)\s*(?:₹|rs\.?|inr)?\s*(\d{2,5})\s*(?:and|to|-)\s*(?:₹|rs\.?|inr)?\s*(\d{2,5})",
    re.IGNORECASE,
)
# Word boundaries are required: without them the "at" inside "eat" matches and
# swallows the rest of the query as an area name.
_NEAR_PLACE = re.compile(r"\b(?:near|in|at|around)\s+([a-z0-9\s]{3,40})$", re.IGNORECASE)

# Stopwords stripped from the residual dish phrase.
_NOISE = {
    "best",
    "top",
    "good",
    "great",
    "nice",
    "the",
    "a",
    "an",
    "in",
    "at",
    "near",
    "me",
    "my",
    "for",
    "of",
    "with",
    "and",
    "or",
    "to",
    "food",
    "place",
    "places",
    "restaurant",
    "restaurants",
    "shop",
    "spot",
    "spots",
    "joint",
    "joints",
    "cheap",
    "budget",
    "affordable",
    "premium",
    "expensive",
    "under",
    "below",
    "over",
    "above",
    "within",
    "upto",
    "around",
    "nearby",
    "famous",
    "must",
    "try",
    "where",
    "which",
    "what",
    "should",
    "i",
    "eat",
    "get",
    "find",
    "some",
    "any",
    "kolkata",
    "city",
    "open",
    "now",
    "today",
    "tonight",
}


@dataclass(slots=True)
class ParsedQuery:
    raw: str
    dish_terms: list[str] = field(default_factory=list)
    cuisine: str | None = None
    area: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    dietary: str | None = None
    mood: str | None = None
    near_me: bool = False
    superlative: bool = False
    price_band: str | None = None
    intent: str = "dish"
    residual: str = ""

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "dish_terms": self.dish_terms,
            "cuisine": self.cuisine,
            "area": self.area,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "dietary": self.dietary,
            "mood": self.mood,
            "near_me": self.near_me,
            "superlative": self.superlative,
            "price_band": self.price_band,
            "intent": self.intent,
        }


def parse_query(query: str, *, known_areas: tuple[str, ...] = DEFAULT_AREAS) -> ParsedQuery:
    """Parse a free-text food query.

    Order matters: multi-word signals (areas, moods, dietary phrases) are removed
    before tokenizing, otherwise "salt lake" would leak "salt" into the dish terms.
    """
    raw = (query or "").strip()
    parsed = ParsedQuery(raw=raw)
    if not raw:
        parsed.intent = "empty"
        return parsed

    working = normalize_name(raw)

    if any(term in working for term in _SUPERLATIVE):
        parsed.superlative = True

    if any(term in working for term in _NEAR_ME):
        parsed.near_me = True
        for term in _NEAR_ME:
            working = working.replace(term, " ")

    if match := _BETWEEN.search(working):
        parsed.min_price = float(match.group(1))
        parsed.max_price = float(match.group(2))
        working = working[: match.start()] + " " + working[match.end() :]
    else:
        if match := _UNDER.search(working):
            parsed.max_price = float(match.group(1))
            working = working[: match.start()] + " " + working[match.end() :]
        if match := _OVER.search(working):
            parsed.min_price = float(match.group(1))
            working = working[: match.start()] + " " + working[match.end() :]

    if any(term in working for term in _CHEAP):
        parsed.price_band = "cheap"
        if parsed.max_price is None:
            parsed.max_price = 300.0
    elif any(term in working for term in _PREMIUM):
        parsed.price_band = "premium"

    # Areas: longest first so "sector v" is not shadowed by a shorter match.
    for area in sorted(known_areas, key=len, reverse=True):
        if area in working:
            parsed.area = area.title()
            working = working.replace(area, " ")
            break
    else:
        if match := _NEAR_PLACE.search(working):
            candidate = match.group(1).strip()
            tokens = [t for t in candidate.split() if t not in _NOISE]
            if tokens and len(" ".join(tokens)) >= 3:
                parsed.area = " ".join(tokens).title()
                working = working[: match.start()] + " " + working[match.end() :]

    for phrase, value in sorted(DIETARY_TERMS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", working):
            parsed.dietary = value
            working = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", working)
            break

    for phrase, value in sorted(MOOD_TERMS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", working):
            parsed.mood = value
            working = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", working)
            break

    for cuisine in sorted(CUISINE_TERMS, key=len, reverse=True):
        if cuisine in working:
            parsed.cuisine = cuisine.title()
            working = working.replace(cuisine, " ")
            break

    tokens = [t for t in working.split() if t and t not in _NOISE and not t.isdigit()]
    parsed.residual = " ".join(tokens)

    if parsed.residual:
        parsed.dish_terms = [parsed.residual]
        parsed.intent = "dish"
    elif parsed.cuisine:
        parsed.intent = "cuisine"
    elif parsed.mood:
        parsed.intent = "mood"
    elif parsed.area:
        parsed.intent = "area"
    else:
        parsed.intent = "browse"

    return parsed


def price_band_bounds(band: str | None) -> tuple[float | None, float | None]:
    return {
        "cheap": (None, 300.0),
        "mid": (150.0, 700.0),
        "premium": (700.0, None),
    }.get(band or "", (None, None))

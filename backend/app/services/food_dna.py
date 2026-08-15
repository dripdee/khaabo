"""Restaurant Food DNA.

Derived entirely from evidence (dish mentions, aspects, prices, trends). No manual
tag input is required or trusted as the primary signal — a manually added tag can
only ever supplement what the data already shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from app.models.enums import TrendDirection

# Chips are ordered by how useful they are for choosing where to eat.
CHIP_ORDER = (
    "signature_dish",
    "spice",
    "price",
    "portion",
    "consistency",
    "ambience",
    "timing",
    "hygiene",
    "trend",
)


@dataclass(frozen=True, slots=True)
class DnaChip:
    code: str
    label: str
    emoji: str
    group: str
    value: float | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "emoji": self.emoji,
            "group": self.group,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class DnaInput:
    dish_labels: list[tuple[str, float, int]]  # (dish name, sentiment 0..1, mentions)
    attribute_counts: dict[str, int]
    aspect_sentiment: dict[str, float]
    price_avg: float | None
    city_median_price: float | None
    consistency: float
    trend: TrendDirection | None
    total_mentions: int
    cuisines: list[str]


MIN_MENTIONS_FOR_DNA = 3
SIGNATURE_SHARE = 0.25
SIGNATURE_MIN_SENTIMENT = 0.7


def build_food_dna(data: DnaInput, *, limit: int = 6) -> list[dict]:
    """Build the chip list. Returns [] when the evidence is too thin.

    An empty DNA is a valid, honest answer; inventing "Affordable" from two
    mentions would make the whole feature untrustworthy.
    """
    if data.total_mentions < MIN_MENTIONS_FOR_DNA:
        return []

    chips: list[DnaChip] = []

    # Signature dish: strong *and* a meaningful share of what people talk about.
    for name, sentiment, mentions in sorted(data.dish_labels, key=lambda row: (-row[2], -row[1]))[
        :3
    ]:
        share = mentions / data.total_mentions if data.total_mentions else 0
        if share >= SIGNATURE_SHARE and sentiment >= SIGNATURE_MIN_SENTIMENT and mentions >= 3:
            chips.append(
                DnaChip(
                    code=f"strong_{name.lower().replace(' ', '_')}",
                    label=f"Strong {name.title()}",
                    emoji=_dish_emoji(name),
                    group="signature_dish",
                    value=round(sentiment, 3),
                )
            )

    counts = data.attribute_counts
    threshold = max(2, int(data.total_mentions * 0.15))

    if counts.get("spicy", 0) >= threshold:
        chips.append(DnaChip("spicy", "Spicy", "🌶️", "spice", counts["spicy"]))
    if counts.get("generous_portion", 0) >= threshold:
        chips.append(
            DnaChip("generous", "Generous Portions", "🍽️", "portion", counts["generous_portion"])
        )
    if counts.get("small_portion", 0) >= threshold:
        chips.append(
            DnaChip("small_portions", "Small Portions", "🤏", "portion", counts["small_portion"])
        )
    if counts.get("oily", 0) >= threshold:
        chips.append(DnaChip("oily", "On the Oily Side", "🛢️", "portion", counts["oily"]))
    if counts.get("late_night", 0) >= 2:
        chips.append(DnaChip("late_night", "Late Night", "🌙", "timing", counts["late_night"]))
    if counts.get("authentic", 0) >= threshold:
        chips.append(DnaChip("authentic", "Authentic", "📜", "ambience", counts["authentic"]))

    # Price positioning relative to the city, never an absolute claim.
    if data.price_avg and data.city_median_price:
        ratio = data.price_avg / data.city_median_price
        if ratio <= 0.75:
            chips.append(DnaChip("affordable", "Affordable", "💰", "price", round(ratio, 3)))
        elif ratio >= 1.4:
            chips.append(DnaChip("premium", "Premium", "💎", "price", round(ratio, 3)))
        else:
            chips.append(DnaChip("mid_range", "Mid Range", "💵", "price", round(ratio, 3)))

    if data.total_mentions >= 8:
        if data.consistency >= 0.75:
            chips.append(
                DnaChip("consistent", "Consistent", "🎯", "consistency", round(data.consistency, 3))
            )
        elif data.consistency <= 0.35:
            chips.append(
                DnaChip(
                    "hit_or_miss", "Hit or Miss", "🎲", "consistency", round(data.consistency, 3)
                )
            )

    service = data.aspect_sentiment.get("service")
    if service is not None and data.total_mentions >= 5:
        if service >= 0.5:
            chips.append(DnaChip("great_service", "Great Service", "🤝", "ambience", service))
        elif service <= -0.4:
            chips.append(DnaChip("slow_service", "Slow Service", "🐢", "ambience", service))

    hygiene = data.aspect_sentiment.get("hygiene")
    if hygiene is not None and hygiene <= -0.4:
        chips.append(DnaChip("hygiene_concerns", "Hygiene Concerns", "⚠️", "hygiene", hygiene))

    ambience = data.aspect_sentiment.get("ambience")
    if ambience is not None and ambience >= 0.5:
        chips.append(DnaChip("good_ambience", "Good Ambience", "✨", "ambience", ambience))

    if data.trend is TrendDirection.RISING:
        chips.append(DnaChip("rising", "On the Rise", "📈", "trend", None))
    elif data.trend is TrendDirection.DECLINING:
        chips.append(DnaChip("declining", "Slipping", "📉", "trend", None))

    if not any(c.group == "signature_dish" for c in chips) and data.cuisines:
        cuisine = data.cuisines[0]
        chips.append(
            DnaChip(
                f"cuisine_{cuisine.lower().replace(' ', '_')}",
                cuisine.title(),
                "🍴",
                "signature_dish",
                None,
            )
        )

    order = {group: i for i, group in enumerate(CHIP_ORDER)}
    chips.sort(key=lambda c: (order.get(c.group, 99), -(c.value or 0)))

    seen: set[str] = set()
    unique: list[DnaChip] = []
    for chip in chips:
        if chip.group in {"price", "consistency", "trend"} and chip.group in seen:
            continue
        seen.add(chip.group)
        unique.append(chip)

    return [chip.to_dict() for chip in unique[:limit]]


_DISH_EMOJI = {
    "momo": "🥟",
    "dumpling": "🥟",
    "dimsum": "🥟",
    "biryani": "🍛",
    "pulao": "🍛",
    "rice": "🍚",
    "ramen": "🍜",
    "noodle": "🍜",
    "chowmein": "🍜",
    "thukpa": "🍜",
    "roll": "🌯",
    "shawarma": "🌯",
    "wrap": "🌯",
    "pizza": "🍕",
    "burger": "🍔",
    "sandwich": "🥪",
    "chicken": "🍗",
    "kebab": "🍢",
    "fish": "🐟",
    "prawn": "🍤",
    "mishti": "🍮",
    "sweet": "🍮",
    "rasgulla": "🍮",
    "ice cream": "🍨",
    "coffee": "☕",
    "tea": "🍵",
    "chai": "🍵",
    "lassi": "🥤",
    "paratha": "🫓",
    "luchi": "🫓",
    "naan": "🫓",
    "dosa": "🥞",
    "puchka": "🥔",
    "chaat": "🥔",
    "cutlet": "🍤",
}


def _dish_emoji(name: str) -> str:
    lowered = name.lower()
    for key, emoji in _DISH_EMOJI.items():
        if key in lowered:
            return emoji
    return "🍽️"


def aggregate_aspect_sentiment(rows: list[tuple[str, float]]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for aspect, sentiment in rows:
        grouped.setdefault(aspect, []).append(sentiment)
    return {aspect: round(fmean(values), 4) for aspect, values in grouped.items()}

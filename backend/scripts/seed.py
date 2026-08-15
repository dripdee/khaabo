"""Seed cities, dishes and dish aliases.

Seed data lives here rather than in a migration so re-seeding never fights schema
history. The script is idempotent: it upserts by slug, so running it repeatedly is
safe and it can be used to onboard a new city later.

Usage:
    python -m scripts.seed --city kolkata
    python -m scripts.seed --city pune --name Pune --lat 18.5204 --lng 73.8567
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.db.session import db_session
from app.models import City, Dish, DishAlias
from app.models.enums import DishCategory
from app.utils.text import normalize_name, slugify

configure_logging()
log = get_logger(__name__)

CITIES: dict[str, dict] = {
    "kolkata": {
        "name": "Kolkata",
        "country": "IN",
        "lat": 22.5726,
        "lng": 88.3639,
        "radius_m": 25000,
        "timezone": "Asia/Kolkata",
    },
}

# (name, cuisine, category, is_veg, aliases)
# `is_veg=None` means the dish exists in both forms — momo is the canonical example,
# and forcing a boolean there would make dietary filters lie.
DISHES: list[tuple[str, str, DishCategory, bool | None, list[str]]] = [
    # ── Tibetan / Nepali / Chinese ────────────────────────────────────────────
    (
        "Chicken Momo",
        "Tibetan",
        DishCategory.STREET_FOOD,
        False,
        ["chicken momo", "chicken momos", "chicken dumpling", "চিকেন মোমো"],
    ),
    (
        "Veg Momo",
        "Tibetan",
        DishCategory.STREET_FOOD,
        True,
        ["veg momo", "vegetable momo", "veg momos"],
    ),
    (
        "Steamed Momo",
        "Tibetan",
        DishCategory.STREET_FOOD,
        None,
        ["momo", "momos", "mo mo", "মোমো", "मोमो", "dimsum", "dim sum"],
    ),
    (
        "Pan Fried Momo",
        "Tibetan",
        DishCategory.STREET_FOOD,
        None,
        ["pan fried momo", "fried momo", "kothey momo"],
    ),
    ("Thukpa", "Tibetan", DishCategory.MAIN, None, ["thukpa", "thenthuk"]),
    (
        "Chicken Chowmein",
        "Chinese",
        DishCategory.MAIN,
        False,
        ["chicken chowmein", "chicken chow mein", "chicken noodles"],
    ),
    ("Hakka Noodles", "Chinese", DishCategory.MAIN, None, ["hakka noodles", "hakka chowmein"]),
    ("Chilli Chicken", "Chinese", DishCategory.MAIN, False, ["chilli chicken", "chili chicken"]),
    ("Chicken Manchurian", "Chinese", DishCategory.MAIN, False, ["chicken manchurian"]),
    # ── Biryani / Mughlai ─────────────────────────────────────────────────────
    (
        "Chicken Biryani",
        "Mughlai",
        DishCategory.MAIN,
        False,
        ["chicken biryani", "biryani", "biriyani", "biriani", "বিরিয়ানি", "बिरयानी"],
    ),
    (
        "Mutton Biryani",
        "Mughlai",
        DishCategory.MAIN,
        False,
        ["mutton biryani", "mutton biriyani", "kosha biryani"],
    ),
    ("Chicken Chaap", "Mughlai", DishCategory.MAIN, False, ["chicken chaap", "chicken chap"]),
    ("Mutton Rezala", "Mughlai", DishCategory.MAIN, False, ["rezala", "mutton rezala"]),
    ("Chicken Rezala", "Mughlai", DishCategory.MAIN, False, ["chicken rezala"]),
    (
        "Mughlai Paratha",
        "Mughlai",
        DishCategory.SNACK,
        False,
        ["mughlai paratha", "moglai paratha", "moghlai porota"],
    ),
    # ── Bengali ───────────────────────────────────────────────────────────────
    (
        "Kosha Mangsho",
        "Bengali",
        DishCategory.MAIN,
        False,
        ["kosha mangsho", "kosha mansho", "কষা মাংস", "bengali mutton curry"],
    ),
    (
        "Shorshe Ilish",
        "Bengali",
        DishCategory.MAIN,
        False,
        ["shorshe ilish", "sorshe ilish", "ilish", "hilsa", "ইলিশ"],
    ),
    ("Macher Jhol", "Bengali", DishCategory.MAIN, False, ["macher jhol", "fish curry", "মাছের ঝোল"]),
    (
        "Chingri Malaikari",
        "Bengali",
        DishCategory.MAIN,
        False,
        ["chingri malaikari", "prawn malaikari", "malai curry"],
    ),
    (
        "Luchi Aloor Dom",
        "Bengali",
        DishCategory.BREAKFAST,
        True,
        ["luchi aloor dom", "luchi alur dom", "luchi", "লুচি"],
    ),
    (
        "Kathi Roll",
        "Bengali",
        DishCategory.STREET_FOOD,
        None,
        ["kathi roll", "kati roll", "roll", "egg roll", "chicken roll", "রোল"],
    ),
    (
        "Puchka",
        "Bengali",
        DishCategory.STREET_FOOD,
        True,
        ["puchka", "phuchka", "pani puri", "golgappa", "ফুচকা"],
    ),
    (
        "Telebhaja",
        "Bengali",
        DishCategory.SNACK,
        True,
        ["telebhaja", "beguni", "aloo chop", "তেলেভাজা"],
    ),
    (
        "Fish Fry",
        "Bengali",
        DishCategory.SNACK,
        False,
        ["fish fry", "fish kabiraji", "kabiraji cutlet"],
    ),
    ("Chicken Cutlet", "Bengali", DishCategory.SNACK, False, ["chicken cutlet", "cutlet"]),
    ("Ghugni", "Bengali", DishCategory.SNACK, True, ["ghugni", "ঘুগনি"]),
    ("Jhalmuri", "Bengali", DishCategory.SNACK, True, ["jhalmuri", "jhal muri", "ঝালমুড়ি"]),
    # ── Sweets ────────────────────────────────────────────────────────────────
    (
        "Rasgulla",
        "Bengali",
        DishCategory.DESSERT,
        True,
        ["rasgulla", "roshogolla", "rosogolla", "রসগোল্লা"],
    ),
    (
        "Mishti Doi",
        "Bengali",
        DishCategory.DESSERT,
        True,
        ["mishti doi", "misti doi", "sweet yogurt", "মিষ্টি দই"],
    ),
    ("Sandesh", "Bengali", DishCategory.DESSERT, True, ["sandesh", "shondesh", "সন্দেশ"]),
    (
        "Nolen Gurer Sandesh",
        "Bengali",
        DishCategory.DESSERT,
        True,
        ["nolen gurer sandesh", "nolen gur sandesh", "jaggery sandesh"],
    ),
    ("Ice Cream", "Continental", DishCategory.DESSERT, True, ["ice cream", "icecream", "sundae"]),
    # ── North / South Indian ──────────────────────────────────────────────────
    (
        "Butter Chicken",
        "North Indian",
        DishCategory.MAIN,
        False,
        ["butter chicken", "murgh makhani"],
    ),
    (
        "Paneer Butter Masala",
        "North Indian",
        DishCategory.MAIN,
        True,
        ["paneer butter masala", "paneer makhani", "shahi paneer"],
    ),
    ("Dal Makhani", "North Indian", DishCategory.MAIN, True, ["dal makhani", "daal makhani"]),
    (
        "Tandoori Chicken",
        "North Indian",
        DishCategory.MAIN,
        False,
        ["tandoori chicken", "tandoori murgh"],
    ),
    ("Chicken Tikka", "North Indian", DishCategory.SNACK, False, ["chicken tikka", "murgh tikka"]),
    (
        "Seekh Kebab",
        "Mughlai",
        DishCategory.SNACK,
        False,
        ["seekh kebab", "seekh kabab", "kebab", "kabab"],
    ),
    ("Masala Dosa", "South Indian", DishCategory.BREAKFAST, True, ["masala dosa", "dosa", "dosai"]),
    ("Idli Sambar", "South Indian", DishCategory.BREAKFAST, True, ["idli", "idli sambar", "idly"]),
    ("Thali", "North Indian", DishCategory.MAIN, None, ["thali", "meal thali", "veg thali"]),
    # ── Global ────────────────────────────────────────────────────────────────
    (
        "Ramen",
        "Japanese",
        DishCategory.MAIN,
        None,
        ["ramen", "tonkotsu ramen", "shoyu ramen", "miso ramen"],
    ),
    ("Sushi", "Japanese", DishCategory.MAIN, None, ["sushi", "maki", "nigiri"]),
    ("Pad Thai", "Thai", DishCategory.MAIN, None, ["pad thai", "phad thai"]),
    (
        "Margherita Pizza",
        "Italian",
        DishCategory.MAIN,
        True,
        ["margherita pizza", "margherita", "pizza margherita"],
    ),
    ("Pepperoni Pizza", "Italian", DishCategory.MAIN, False, ["pepperoni pizza", "pepperoni"]),
    ("Carbonara", "Italian", DishCategory.MAIN, False, ["carbonara", "pasta carbonara"]),
    (
        "Chicken Burger",
        "American",
        DishCategory.MAIN,
        False,
        ["chicken burger", "burger", "chicken sandwich"],
    ),
    ("Shawarma", "Lebanese", DishCategory.STREET_FOOD, False, ["shawarma", "shawerma"]),
    ("Falafel", "Lebanese", DishCategory.SNACK, True, ["falafel", "falafel wrap"]),
    ("Banh Mi", "Vietnamese", DishCategory.MAIN, None, ["banh mi", "bahn mi"]),
    # ── Cafe ──────────────────────────────────────────────────────────────────
    (
        "Cold Coffee",
        "Cafe",
        DishCategory.BEVERAGE,
        True,
        ["cold coffee", "iced coffee", "cold brew"],
    ),
    ("Cappuccino", "Cafe", DishCategory.BEVERAGE, True, ["cappuccino", "capuccino"]),
    (
        "Filter Coffee",
        "South Indian",
        DishCategory.BEVERAGE,
        True,
        ["filter coffee", "filter kaapi", "kaapi"],
    ),
    (
        "Masala Chai",
        "Cafe",
        DishCategory.BEVERAGE,
        True,
        ["masala chai", "chai", "cha", "চা", "tea"],
    ),
    ("Lassi", "North Indian", DishCategory.BEVERAGE, True, ["lassi", "sweet lassi"]),
    ("Cheesecake", "Cafe", DishCategory.DESSERT, True, ["cheesecake", "cheese cake"]),
    ("Croissant", "Cafe", DishCategory.BREAKFAST, True, ["croissant", "butter croissant"]),
    (
        "English Breakfast",
        "Continental",
        DishCategory.BREAKFAST,
        False,
        ["english breakfast", "full english", "big breakfast"],
    ),
]


async def seed_city(slug: str, overrides: dict | None = None) -> None:
    config = {**CITIES.get(slug, {}), **(overrides or {})}
    if not config.get("name"):
        raise SystemExit(f"Unknown city '{slug}'. Pass --name/--lat/--lng to onboard a new one.")

    async with db_session() as session:
        city = (await session.execute(select(City).where(City.slug == slug))).scalar_one_or_none()

        point = f"SRID=4326;POINT({config['lng']} {config['lat']})"

        if city is None:
            city = City(
                name=config["name"],
                slug=slug,
                country=config.get("country", "IN"),
                center=point,
                lat=config["lat"],
                lng=config["lng"],
                radius_m=config.get("radius_m", 25000),
                timezone=config.get("timezone", "Asia/Kolkata"),
                active=True,
            )
            session.add(city)
            log.info("city_created", slug=slug)
        else:
            city.name = config["name"]
            city.center = point
            city.lat = config["lat"]
            city.lng = config["lng"]
            city.radius_m = config.get("radius_m", city.radius_m)
            city.timezone = config.get("timezone", city.timezone)
            city.active = True
            log.info("city_updated", slug=slug)


async def seed_dishes() -> tuple[int, int]:
    """Upsert the dish taxonomy and its aliases. Safe to re-run."""
    created = 0
    aliases_created = 0

    async with db_session() as session:
        for name, cuisine, category, is_veg, aliases in DISHES:
            slug = slugify(name)
            dish = (
                await session.execute(select(Dish).where(Dish.slug == slug))
            ).scalar_one_or_none()

            if dish is None:
                dish = Dish(
                    name=name,
                    slug=slug,
                    normalized_name=normalize_name(name),
                    cuisine=cuisine,
                    category=category,
                    is_veg=is_veg,
                )
                session.add(dish)
                await session.flush()
                created += 1
            else:
                dish.cuisine = cuisine
                dish.category = category
                dish.is_veg = is_veg

            for alias in aliases:
                normalized = normalize_name(alias)
                if not normalized:
                    continue

                # UNIQUE(normalized_alias, lang) guarantees one dish per surface form.
                # A collision means two dishes claim the same alias, which would make
                # extraction ambiguous, so the first definition wins and we warn.
                existing = (
                    await session.execute(
                        select(DishAlias).where(
                            DishAlias.normalized_alias == normalized,
                            DishAlias.lang == "en",
                        )
                    )
                ).scalar_one_or_none()

                if existing is not None:
                    if existing.dish_id != dish.id:
                        log.warning(
                            "alias_collision_skipped",
                            alias=alias,
                            claimed_by=str(existing.dish_id),
                            requested_by=dish.slug,
                        )
                    continue

                session.add(
                    DishAlias(
                        dish_id=dish.id,
                        alias=alias,
                        normalized_alias=normalized,
                        lang="en",
                        weight=1.0 if normalized == normalize_name(name) else 0.9,
                    )
                )
                aliases_created += 1

    return created, aliases_created


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed cities and the dish taxonomy")
    parser.add_argument("--city", default="kolkata", help="City slug to seed")
    parser.add_argument("--name", help="City display name (for a new city)")
    parser.add_argument("--lat", type=float, help="City centre latitude")
    parser.add_argument("--lng", type=float, help="City centre longitude")
    parser.add_argument("--country", default="IN")
    parser.add_argument("--timezone", default="Asia/Kolkata")
    parser.add_argument("--radius-m", type=int, default=25000)
    parser.add_argument("--dishes-only", action="store_true")
    args = parser.parse_args()

    if not args.dishes_only:
        overrides: dict = {}
        if args.name:
            overrides["name"] = args.name
        if args.lat is not None:
            overrides["lat"] = args.lat
        if args.lng is not None:
            overrides["lng"] = args.lng
        overrides["country"] = args.country
        overrides["timezone"] = args.timezone
        overrides["radius_m"] = args.radius_m
        await seed_city(args.city, overrides)

    dishes, aliases = await seed_dishes()
    log.info("seed_complete", city=args.city, dishes_created=dishes, aliases_created=aliases)
    print(f"Seeded city '{args.city}': {dishes} new dishes, {aliases} new aliases.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

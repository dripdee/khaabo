"""Generate sitemap.xml for SEO.

Logs every dish and restaurant slug — these are the only SEO-relevant deep
links in the app (search and profile pages are behind auth or ephemeral).
Filters are kept simple and explicit so the output is auditable: a sitemap is a
public declaration of which pages exist.

Usage:
    python -m scripts.generate_sitemap --output ./frontend/public/sitemap.xml
    python -m scripts.generate_sitemap --base-url https://khaabo.in

The script reads from the database (sync session), so it must run on the server
or against a replica. Output is written to stdout by default; redirect or pass
--output.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.db.session import sync_session
from app.models import Dish, Restaurant

configure_logging()
log = get_logger(__name__)

FRONTEND_ROUTES = [
    ("/", 1.0, None),  # home / search
    ("/trending", 0.7, None),
]


def generate(base_url: str, output: str | None = None) -> int:
    """Write the sitemap; return the number of URLs included."""
    base = base_url.rstrip("/")

    urls: list[tuple[str, float, str | None]] = []

    for route, priority, lastmod in FRONTEND_ROUTES:
        urls.append((base + route, priority, lastmod))

    count = 0
    with sync_session() as session:
        dishes = session.execute(select(Dish.slug, Dish.updated_at).order_by(Dish.slug)).all()
        for slug, updated in dishes:
            lastmod = updated.strftime("%Y-%m-%d") if updated else None
            urls.append((f"{base}/dish/{slug}", 0.8, lastmod))
            count += 1

        restaurants = session.execute(
            select(Restaurant.slug, Restaurant.updated_at).order_by(Restaurant.slug)
        ).all()
        for slug, updated in restaurants:
            lastmod = updated.strftime("%Y-%m-%d") if updated else None
            urls.append((f"{base}/restaurant/{slug}", 0.6, lastmod))
            count += 1

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url, _priority, lastmod in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(url)}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        else:
            lines.append(f"    <lastmod>{datetime.now(UTC).strftime('%Y-%m-%d')}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")

    xml = "\n".join(lines) + "\n"

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(xml)
        log.info("sitemap_written", path=output, urls=len(urls))
    else:
        sys.stdout.write(xml)

    return len(urls)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sitemap.xml")
    parser.add_argument(
        "--base-url",
        default="https://khaabo.in",
        help="Base URL of the frontend (default: https://khaabo.in)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    total_urls = generate(args.base_url, args.output)
    log.info("sitemap_done", urls=total_urls, base_url=args.base_url)


if __name__ == "__main__":
    main()

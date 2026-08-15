"""Reddit adapter — official API only.

Uses the OAuth client-credentials flow with a script app (free). Only public
listings are read. Posts are pre-filtered against the city and dish vocabulary so
irrelevant threads are skipped before any storage or AI work.

Nothing here scrapes HTML or bypasses the API.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from app.core.config import settings
from app.core.errors import PermanentSourceError, TransientSourceError
from app.core.logging import get_logger
from app.ingestion.base import CityRef, RateLimiter, RawReview, SourceAdapter, request_json
from app.models.enums import SourceType
from app.utils.text import clean_text, normalize_name

log = get_logger(__name__)

REDDIT_LICENSE = "reddit-api-terms"
REDDIT_ATTRIBUTION = "Reddit"

OAUTH_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"

MIN_BODY_LENGTH = 40
MAX_COMMENTS_PER_POST = 50
COMMENT_DEPTH = 2

_limiter = RateLimiter(60.0 / max(1, settings.reddit_requests_per_minute))

# Only keep items that look like they are about eating something somewhere.
_FOOD_CUES = (
    "restaurant",
    "cafe",
    "eat",
    "food",
    "dish",
    "tasty",
    "menu",
    "order",
    "biryani",
    "momo",
    "roll",
    "kebab",
    "thali",
    "breakfast",
    "dinner",
    "lunch",
    "street food",
    "must try",
    "recommend",
)


class RedditAdapter(SourceAdapter):
    source = SourceType.REDDIT

    def __init__(self) -> None:
        self.default_interval_hours = settings.source_interval_reddit_hours
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(settings.reddit_client_id and settings.reddit_client_secret)

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not self.configured:
            raise PermanentSourceError("Reddit credentials are not configured")

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                OAUTH_URL,
                auth=(settings.reddit_client_id, settings.reddit_client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": settings.reddit_user_agent},
            )
        if resp.status_code == 429:
            raise TransientSourceError("Reddit token endpoint rate limited")
        if resp.status_code >= 400:
            raise PermanentSourceError(f"Reddit auth failed: {resp.status_code}")

        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    async def fetch_reviews(self, city: CityRef, since: datetime | None = None) -> list[RawReview]:
        if not self.configured:
            log.info("reddit_skipped_not_configured")
            return []

        token = await self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": settings.reddit_user_agent,
        }
        cutoff = since.timestamp() if since else 0.0
        city_terms = {normalize_name(city.name), normalize_name(city.slug)}

        reviews: list[RawReview] = []

        async with httpx.AsyncClient(timeout=30.0, base_url=API_BASE) as client:
            for subreddit in settings.reddit_subreddit_list:
                try:
                    listing = await request_json(
                        client,
                        "GET",
                        f"/r/{subreddit}/new",
                        limiter=_limiter,
                        headers=headers,
                        params={"limit": 100, "raw_json": 1},
                    )
                except TransientSourceError:
                    raise
                except PermanentSourceError as exc:
                    log.warning("reddit_subreddit_failed", subreddit=subreddit, error=str(exc))
                    continue

                for child in listing.get("data", {}).get("children", []):
                    post = child.get("data") or {}
                    created = float(post.get("created_utc") or 0)
                    if created <= cutoff:
                        continue

                    is_city_sub = normalize_name(subreddit) in city_terms
                    if not self._is_relevant(post, city_terms, lenient=is_city_sub):
                        continue

                    post_review = self._post_to_review(post, subreddit)
                    if post_review:
                        reviews.append(post_review)

                    reviews.extend(
                        await self._fetch_comments(client, headers, subreddit, post, cutoff)
                    )

        log.info("reddit_fetched", count=len(reviews), city=city.slug)
        return reviews

    def _is_relevant(self, post: dict, city_terms: set[str], *, lenient: bool) -> bool:
        """Relevance gate.

        In a city subreddit we only need a food cue; elsewhere the city must also be
        named, otherwise a national thread would pollute a city's evidence.
        """
        blob = normalize_name(f"{post.get('title', '')} {post.get('selftext', '')}")
        if not blob:
            return False
        has_food = any(cue in blob for cue in _FOOD_CUES)
        if not has_food:
            return False
        if lenient:
            return True
        return any(term and term in blob for term in city_terms)

    def _post_to_review(self, post: dict, subreddit: str) -> RawReview | None:
        body = clean_text(post.get("selftext") or "")
        title = clean_text(post.get("title") or "")
        if len(body) < MIN_BODY_LENGTH:
            # A bare title is a question, not evidence about a dish.
            return None
        if post.get("over_18") or post.get("removed_by_category"):
            return None

        return RawReview(
            source=SourceType.REDDIT,
            external_id=f"t3_{post['id']}",
            title=title or None,
            text=f"{title}. {body}" if title else body,
            author=post.get("author"),
            published_at=datetime.fromtimestamp(float(post["created_utc"]), tz=UTC),
            engagement=int(post.get("score") or 0),
            url=f"https://reddit.com{post.get('permalink', '')}",
            permalink=post.get("permalink"),
            license=REDDIT_LICENSE,
            attribution=f"r/{subreddit}",
            raw={"subreddit": subreddit, "num_comments": post.get("num_comments")},
        )

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        subreddit: str,
        post: dict,
        cutoff: float,
    ) -> list[RawReview]:
        """Top comments only, shallow depth — that is where the recommendations are."""
        post_id = post.get("id")
        if not post_id or int(post.get("num_comments") or 0) == 0:
            return []

        try:
            payload = await request_json(
                client,
                "GET",
                f"/r/{subreddit}/comments/{post_id}",
                limiter=_limiter,
                headers=headers,
                params={
                    "limit": MAX_COMMENTS_PER_POST,
                    "depth": COMMENT_DEPTH,
                    "sort": "top",
                    "raw_json": 1,
                },
            )
        except (TransientSourceError, PermanentSourceError) as exc:
            log.warning("reddit_comments_failed", post_id=post_id, error=str(exc))
            return []

        listings = payload if isinstance(payload, list) else [payload]
        out: list[RawReview] = []

        for listing in listings[1:]:  # index 0 is the post itself
            for child in listing.get("data", {}).get("children", []):
                comment = child.get("data") or {}
                if child.get("kind") != "t1":
                    continue
                body = clean_text(comment.get("body") or "")
                created = float(comment.get("created_utc") or 0)
                if len(body) < MIN_BODY_LENGTH or created <= cutoff:
                    continue
                if body.lower() in {"[deleted]", "[removed]"}:
                    continue

                out.append(
                    RawReview(
                        source=SourceType.REDDIT,
                        external_id=f"t1_{comment['id']}",
                        text=body,
                        author=comment.get("author"),
                        published_at=datetime.fromtimestamp(created, tz=UTC),
                        engagement=int(comment.get("score") or 0),
                        url=f"https://reddit.com{comment.get('permalink', '')}",
                        permalink=comment.get("permalink"),
                        license=REDDIT_LICENSE,
                        attribution=f"r/{subreddit}",
                        raw={"subreddit": subreddit, "parent_post": post_id},
                    )
                )

        return out

    async def health(self) -> bool:
        if not self.configured:
            return False
        try:
            await self._get_token()
            return True
        except Exception:
            return False

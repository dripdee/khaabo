"""Text normalization shared by ingestion, extraction and entity resolution.

Kept dependency-free and deterministic so it can be unit tested and reused by
both the API and the workers.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\ufeff\u2060]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MARKDOWN = re.compile(r"[*_~`>#\[\]()]")
_MULTI_NEWLINE = re.compile(r"\n{3,}")

# Legal/brand suffixes that add nothing to identity matching.
_NAME_NOISE = (
    "pvt ltd",
    "private limited",
    "pvt. ltd.",
    "llp",
    "restaurant and bar",
    "restaurant & bar",
    "family restaurant",
)

# Digit-boundary guards matter: without them "₹99999999" matches its first five
# digits and silently invents a ₹99,999 price signal.
_PRICE_PATTERNS = (
    re.compile(r"(?:₹|rs\.?|inr)\s*(\d{1,5}(?:\.\d{1,2})?)(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,5}(?:\.\d{1,2})?)\s*(?:₹|rs\.?|inr|/-)", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,5})\s*(?:bucks|rupees)", re.IGNORECASE),
)


def clean_text(text: str, *, max_length: int = 8000) -> str:
    """Light cleanup that preserves meaning: markup and noise out, words intact."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = _URL.sub(" ", text)
    text = _MARKDOWN.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    text = _WS.sub(" ", text).strip()
    return text[:max_length]


def normalize_name(name: str) -> str:
    """Aggressive fold for matching: lowercase, unaccented, punctuation-free."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    for noise in _NAME_NOISE:
        if text.endswith(noise):
            text = text[: -len(noise)].strip()
    return text


def slugify(value: str, *, max_length: int = 120) -> str:
    base = normalize_name(value).replace(" ", "-")
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:max_length] or "item"


def content_hash(*parts: object) -> str:
    """Stable hash for exact-duplicate detection.

    Text parts are normalized first so whitespace/case variants collapse to one
    hash — otherwise the same review reposted with different spacing would slip
    past the UNIQUE constraint.
    """
    payload = "|".join(
        normalize_name(str(p)) if isinstance(p, str) else str(p) for p in parts if p is not None
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_prices(text: str) -> list[float]:
    """Pull currency amounts out of free text. Used to gate AI price claims."""
    found: list[float] = []
    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(text or ""):
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 100000:
                found.append(value)
    return found


def has_price_signal(text: str) -> bool:
    return bool(extract_prices(text))


def tokenize(text: str) -> list[str]:
    return [t for t in normalize_name(text).split(" ") if t]


def token_set(text: str) -> set[str]:
    return set(tokenize(text))

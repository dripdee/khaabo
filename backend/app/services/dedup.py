"""Duplicate detection.

Three layers, cheapest first:
1. source identity      — enforced by UNIQUE (source, external_id) in the DB
2. exact content hash   — enforced by UNIQUE (reviews.content_hash)
3. near-duplicate       — simhash prefilter + token-set Jaccard confirmation

Layer 3 lives here because it needs judgement: a low Hamming distance is only a
*candidate*, and confirming with Jaccard avoids collapsing genuinely different
reviews that happen to share vocabulary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.utils.text import clean_text, content_hash, token_set, tokenize

SIMHASH_BITS = 64
# Measured on realistic review pairs with the weighting below: single-word edits land
# at 6-9 bits, genuinely different reviews at 17+. 12 sits inside that gap. It is a
# *candidate* threshold only — every hit is then confirmed by token-set Jaccard, which
# is what actually protects against false positives (e.g. two reviews of the same dish
# with opposite verdicts score 17 bits apart but only 0.69 Jaccard, below the 0.82 bar).
HAMMING_THRESHOLD = 12
JACCARD_THRESHOLD = 0.82
SHINGLE_SIZE = 2
UNIGRAM_WEIGHT = 2
BIGRAM_WEIGHT = 1


def simhash(text: str, *, bits: int = SIMHASH_BITS) -> int:
    """Charikar simhash over weighted unigrams and bigrams.

    Unigrams carry double weight so a single substituted word does not dominate the
    signature; bigrams still contribute enough word order that the same vocabulary
    rearranged is not automatically a duplicate. A shingle-only signature was tried
    first and made one-word edits look as distant as unrelated text on short reviews.
    """
    tokens = tokenize(text)
    if not tokens:
        return 0

    features: list[tuple[str, int]] = [(token, UNIGRAM_WEIGHT) for token in tokens]
    features.extend(
        (" ".join(tokens[i : i + SHINGLE_SIZE]), BIGRAM_WEIGHT)
        for i in range(len(tokens) - SHINGLE_SIZE + 1)
    )

    vector = [0] * bits
    for feature, weight in features:
        digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        for i in range(bits):
            vector[i] += weight if (digest >> i) & 1 else -weight

    value = 0
    for i, weight in enumerate(vector):
        if weight > 0:
            value |= 1 << i
    return value


def to_signed_64(value: int) -> int:
    """Postgres BIGINT is signed; fold the unsigned simhash into range."""
    value &= (1 << 64) - 1
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming_distance(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << SIMHASH_BITS) - 1)).count("1")


def jaccard(a: str, b: str) -> float:
    set_a, set_b = token_set(a), token_set(b)
    if not set_a or not set_b:
        return 0.0
    union = set_a | set_b
    return len(set_a & set_b) / len(union)


@dataclass(frozen=True, slots=True)
class DuplicateVerdict:
    is_duplicate: bool
    method: str
    similarity: float
    matched_id: str | None = None


def review_fingerprint(body: str, author: str | None, published_at: object | None) -> str:
    """Exact-duplicate key. Text is normalized first, so whitespace/case variants
    of the same review collapse to one hash instead of slipping past UNIQUE."""
    return content_hash(clean_text(body), author, published_at)


def is_near_duplicate(
    candidate_text: str,
    existing: list[tuple[str, int, str]],
    *,
    hamming_threshold: int = HAMMING_THRESHOLD,
    jaccard_threshold: float = JACCARD_THRESHOLD,
) -> DuplicateVerdict:
    """Compare a candidate against `(id, simhash, text)` rows from the same
    restaurant and time neighbourhood.

    Two-stage on purpose: simhash is cheap and indexable but approximate, so every
    hit is confirmed with an exact token-set overlap before anything is discarded.
    """
    cand_hash = simhash(candidate_text)
    best = DuplicateVerdict(is_duplicate=False, method="none", similarity=0.0)

    for row_id, row_hash, row_text in existing:
        distance = hamming_distance(cand_hash, row_hash)
        if distance > hamming_threshold:
            continue
        similarity = jaccard(candidate_text, row_text)
        if similarity >= jaccard_threshold and similarity > best.similarity:
            best = DuplicateVerdict(
                is_duplicate=True,
                method="simhash+jaccard",
                similarity=round(similarity, 4),
                matched_id=row_id,
            )

    return best


# ── spam heuristics ──────────────────────────────────────────────────────────

_CONTACT = ("whatsapp", "call now", "dm me", "click here", "http", "www.", "@gmail", "+91")
_PROMO = ("discount", "offer valid", "coupon", "promo code", "free delivery on", "subscribe")


def spam_score(text: str, *, link_count: int = 0) -> float:
    """Cheap, explainable spam signal in [0, 1].

    Deliberately conservative: it feeds the moderation queue rather than auto-
    rejecting, because false positives silence real users.
    """
    body = (text or "").strip()
    if not body:
        return 1.0

    score = 0.0
    lowered = body.lower()
    tokens = tokenize(body)

    if len(tokens) < 4:
        score += 0.35

    if tokens:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < 0.3:
            score += 0.45
        elif unique_ratio < 0.5:
            score += 0.25

    contact_hits = sum(1 for marker in _CONTACT if marker in lowered)
    score += min(0.4, contact_hits * 0.2)

    promo_hits = sum(1 for marker in _PROMO if marker in lowered)
    score += min(0.45, promo_hits * 0.18)

    score += min(0.2, link_count * 0.1)

    letters = [c for c in body if c.isalpha()]
    if len(letters) >= 12:
        caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if caps_ratio > 0.7:
            score += 0.15

    if any(ch * 5 in lowered for ch in "abcdefghijklmnopqrstuvwxyz"):
        score += 0.15

    return round(min(1.0, score), 4)


def is_spam(text: str, *, threshold: float = 0.6, link_count: int = 0) -> bool:
    return spam_score(text, link_count=link_count) >= threshold

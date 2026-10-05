"""Deterministic local text embeddings.

Agents must retrieve relevant memories without shipping their entire history
into a prompt. A real deployment would use a sentence-transformer; to keep the
ecosystem fully local and dependency-free we use a signed hashing vectoriser,
which is deterministic, needs no model weights, and ranks by lexical overlap.

The interface matches what a neural embedder would provide, so swapping one in
later touches only this file.
"""

from __future__ import annotations

import hashlib
import math
import re

from ecosystem.db.models.types import EMBEDDING_DIM

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Common words carry no retrieval signal.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "been", "it", "this", "that", "with", "as", "at",
    "by", "from", "we", "i", "you", "they", "he", "she", "not", "no", "but",
}


def tokenize(text: str) -> list[str]:
    return [
        t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1
    ]


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.sha256(token.encode()).digest()
    index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def embed(text: str) -> list[float]:
    """Return a unit-norm EMBEDDING_DIM vector for the text."""
    vector = [0.0] * EMBEDDING_DIM
    tokens = tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        index, sign = _bucket(token)
        vector[index] += sign
        # Bigrams add a little word-order signal without a real model.
    for first, second in zip(tokens, tokens[1:]):
        index, sign = _bucket(f"{first}_{second}")
        vector[index] += 0.5 * sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

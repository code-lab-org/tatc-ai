"""Name normalization and relevance scoring shared by catalog clients."""

import re


def normalize_name(value: str) -> str:
    """Normalize a name for case-insensitive matching."""
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", value.upper()).split())


def score_match(query: str, candidate_name: str) -> int:
    """Score a candidate name against a query for deterministic ranking."""
    normalized_query = normalize_name(query)
    normalized_candidate = normalize_name(candidate_name)
    if not normalized_query or not normalized_candidate:
        return 0

    query_tokens = normalized_query.split()
    candidate_tokens = normalized_candidate.split()
    score = 0

    if normalized_candidate == normalized_query:
        score += 100
    if normalized_candidate.startswith(normalized_query):
        score += 40
    if normalized_query in normalized_candidate:
        score += 20
    if all(token in candidate_tokens for token in query_tokens):
        score += 15
    if any(token == normalized_query for token in candidate_tokens):
        score += 10

    # Prefer tighter matches when the score would otherwise tie.
    score -= abs(len(candidate_tokens) - len(query_tokens))
    return score

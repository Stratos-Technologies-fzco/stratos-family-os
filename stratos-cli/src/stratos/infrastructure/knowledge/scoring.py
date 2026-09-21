"""Shared relevance ranking for every knowledge source."""

import math
import re
from collections.abc import Iterable

from stratos.domain.models.extensions import KnowledgeHit
from stratos.utils.redaction import SecretRedactor

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by for from how in is it of on or "
    "that the to was what when where which who why with".split()
)


def tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


def query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(tokens(query)))


def markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def snippet(text: str, terms: list[str], redactor: SecretRedactor) -> str:
    for line in text.splitlines():
        low = line.lower()
        if any(t in low for t in terms):
            return redactor.redact_text(line.strip().lstrip("#").strip()[:200])
    return ""


def rank(
    query: str,
    docs: Iterable[tuple[str, str, str]],
    limit: int,
    redactor: SecretRedactor,
) -> list[KnowledgeHit]:
    """Rank (id, title, text) documents: title matches count most, then headings, then body."""
    terms = query_terms(query)
    if not terms:
        return []
    hits: list[KnowledgeHit] = []
    for doc_id, title, text in docs:
        title_tokens = set(tokens(title))
        heading_tokens = tokens(" ".join(ln for ln in text.splitlines() if ln.startswith("#")))
        body = tokens(text)
        score = 0.0
        for term in terms:
            score += 5.0 * (term in title_tokens) + 3.0 * heading_tokens.count(term)
            score += min(body.count(term), 10)
        if score <= 0:
            continue
        score = round(score / (1 + math.log10(1 + len(body))), 3)  # damp very long documents
        hits.append(
            KnowledgeHit(
                id=doc_id, title=title, score=score, snippet=snippet(text, terms, redactor)
            )
        )
    hits.sort(key=lambda h: (-h.score, h.id))
    return hits[:limit]

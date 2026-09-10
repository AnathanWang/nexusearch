"""Generic query-planning helpers (no domain prompts)."""

from __future__ import annotations

import re
from typing import Any


def normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


def dedupe_queries(queries: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if not q:
            continue
        key = normalize_query(q)
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= limit:
            break
    return out


def sanitize_serp_field(text: str, limit: int) -> str:
    """Strip controls and fence-breakers before embedding SERP text in prompts."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
    cleaned = cleaned.replace("```", "'''").replace("<<<", "«««").replace(">>>", "»»»")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def niche_tokens(*texts: str, stopwords: frozenset[str] | None = None) -> set[str]:
    stop = stopwords or frozenset()
    toks: set[str] = set()
    for text in texts:
        for t in re.findall(r"[a-z0-9]{3,}", text.lower()):
            if t not in stop:
                toks.add(t)
    return toks


def has_niche_overlap(query: str, niche: set[str], stopwords: frozenset[str] | None = None) -> bool:
    if not niche:
        return True
    return bool(niche_tokens(query, stopwords=stopwords) & niche)


def extract_queries_from_llm_payload(payload: Any) -> list[str]:
    """Test helper."""
    if isinstance(payload, dict) and isinstance(payload.get("queries"), list):
        return [q for q in payload["queries"] if isinstance(q, str)]
    return []

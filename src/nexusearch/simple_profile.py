"""SimpleSearchProfile — a complete search scenario as declarative config.

The library is a smart but blind search agent; this profile is the "briefing"
the developer hands it: where to search (geo), how to expand queries
(dictionary), how to reject garbage (content policy), and what facts to
extract (hints) — without writing a protocol implementation.

Planning is deterministic (templates only). Consumers needing LLM query
planning should implement the full SearchProfile protocol instead.

Example (HoReCa B2B supplier scenario):

    SimpleSearchProfile(
        name="horeca_b2b",
        description="HoReCa B2B suppliers",
        query_templates=[
            "{query} HoReCa опт поставка",
            "{query} оптовый поставщик прайс-лист",
        ],
        ignored_domains=["eda.ru", "delivery-club."],
        stop_words=["рецепт", "как приготовить", "калорийность"],
        required_words=["прайс", "отгрузка"],
        hint_patterns={"email": EMAIL_PATTERN, "phone": PHONE_PATTERN},
    )
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import HintConfidence, PageSnippet, SearchHit
from nexusearch.planning import dedupe_queries, normalize_query

logger = logging.getLogger(__name__)

_BRAND_WORDS_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")


class SimpleSearchProfile:
    """Declarative SearchProfile implementing the full protocol plus optional
    content-policy hooks (reject_page / accept_domain) and geo filters
    (allowed_domains / url_path_patterns, enforced by NexusSearchClient)."""

    def __init__(
        self,
        *,
        name: str,
        query_templates: Sequence[str],
        description: str = "",
        channels: Sequence[str] = ("serp",),
        iter2_template: str = '"{brand}"',
        ignored_domains: Sequence[str] = (),
        allowed_domains: Sequence[str] = (),
        url_path_patterns: Sequence[str] = (),
        deep_paths: Sequence[str] = ("/",),
        stop_words: Sequence[str] = (),
        required_words: Sequence[str] = (),
        hint_patterns: Mapping[str, str] | None = None,
        int_hints: Sequence[str] = (),
    ) -> None:
        if not name or not name.strip():
            raise ValueError("SimpleSearchProfile requires a non-empty name")
        if not query_templates:
            raise ValueError("SimpleSearchProfile requires at least one query template")
        for t in query_templates:
            if "{query}" not in t:
                raise ValueError(f"query template must contain '{{query}}': {t!r}")
        if "{brand}" not in iter2_template:
            raise ValueError("iter2_template must contain '{brand}'")
        compiled_hints: dict[str, re.Pattern[str]] = {}
        for field, pattern in (hint_patterns or {}).items():
            try:
                compiled_hints[field] = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"invalid hint pattern for {field!r}: {e}") from e
        for p in url_path_patterns:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"invalid url_path_pattern {p!r}: {e}") from e

        self.name = name.strip()
        self.description = description
        self.channels = tuple(channels)
        self.query_templates = tuple(query_templates)
        self.iter2_template = iter2_template
        self.ignored_domains = tuple(ignored_domains)
        self.allowed_domains = tuple(d.lower().strip() for d in allowed_domains if d.strip())
        self.url_path_patterns = tuple(url_path_patterns)
        self.deep_paths = tuple(deep_paths)
        self.stop_words = tuple(w.lower() for w in stop_words if w.strip())
        self.required_words = tuple(w.lower() for w in required_words if w.strip())
        self.hint_patterns = compiled_hints
        self.int_hints = frozenset(int_hints)

    # --- query planning (dictionary) ----------------------------------------

    def plan_iter1(
        self,
        query: str,
        *,
        llm: LlmJsonClient | None = None,
        max_queries: int = 5,
    ) -> list[str]:
        queries = [t.replace("{query}", query.strip()) for t in self.query_templates]
        return dedupe_queries(queries, max_queries)

    def plan_iter2(
        self,
        hits: list[SearchHit],
        *,
        already_used: list[str],
        llm: LlmJsonClient | None = None,
        max_queries: int = 3,
    ) -> list[str]:
        used = {normalize_query(q) for q in already_used}
        out: list[str] = []
        for h in hits[:8]:
            words = _BRAND_WORDS_RE.findall(h.title or "")
            if not words:
                continue
            brand = " ".join(words[:4])
            q = self.iter2_template.replace("{brand}", brand)
            key = normalize_query(q)
            if key in used:
                continue
            used.add(key)
            out.append(q)
            if len(out) >= max_queries:
                break
        return out

    # --- content policy (garbage detector) ----------------------------------

    def reject_page(self, text: str, *, url: str) -> bool:
        """Red flags: any stop word on a fetched page disqualifies the domain."""
        if not self.stop_words:
            return False
        lower = text.lower()
        return any(w in lower for w in self.stop_words)

    def accept_domain(self, texts: Sequence[str], *, domain: str) -> bool:
        """Green flags: with required_words set, at least one must appear in the
        combined full text of all fetched pages of the domain."""
        if not self.required_words:
            return True
        blob = " ".join(texts).lower()
        return any(w in blob for w in self.required_words)

    # --- fact extraction (hints) --------------------------------------------

    def extract_hints(self, text: str, *, url: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field, pattern in self.hint_patterns.items():
            m = pattern.search(text)
            if not m:
                continue
            value = (m.group(1) if m.groups() else m.group(0)).strip()
            if not value:
                continue
            if field in self.int_hints:
                try:
                    out[field] = int(value.replace(",", "").replace(" ", ""))
                except ValueError:
                    continue
            else:
                out[field] = value
        return out

    def score_confidence(
        self,
        pages: list[PageSnippet],
        *,
        pages_with_hints: int,
        has_any_hint: bool,
        contributing_excerpt_len: int = 0,
    ) -> HintConfidence | None:
        """Simple policy: no hints → None; hints on >=2 pages → HIGH; else LOW."""
        if not has_any_hint:
            return None
        return "HIGH" if pages_with_hints >= 2 else "LOW"

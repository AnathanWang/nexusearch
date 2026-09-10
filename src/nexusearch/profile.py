"""SearchProfile protocol — domain specialization lives in the consumer."""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import HintConfidence, PageSnippet, SearchHit


@runtime_checkable
class SearchProfile(Protocol):
    """Consumer-defined search domain (queries, ignores, paths, hint extractors)."""

    name: str
    ignored_domains: Sequence[str]
    deep_paths: Sequence[str]

    def plan_iter1(
        self,
        query: str,
        *,
        llm: LlmJsonClient | None,
        max_queries: int,
    ) -> list[str]: ...

    def plan_iter2(
        self,
        hits: list[SearchHit],
        *,
        already_used: list[str],
        llm: LlmJsonClient | None,
        max_queries: int,
    ) -> list[str]: ...

    def extract_hints(self, text: str, *, url: str) -> dict[str, Any]: ...

    def score_confidence(
        self,
        pages: list[PageSnippet],
        *,
        pages_with_hints: int,
        has_any_hint: bool,
        contributing_excerpt_len: int = 0,
    ) -> HintConfidence | None: ...

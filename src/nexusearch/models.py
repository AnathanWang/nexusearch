"""Pydantic DTOs for nexusearch (domain-agnostic)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HintConfidence = Literal["LOW", "MEDIUM", "HIGH"]


class PageSnippet(BaseModel):
    url: str
    text_excerpt: str = ""


class PageEvidence(BaseModel):
    domain: str
    pages: list[PageSnippet] = Field(default_factory=list)
    extracted: dict[str, Any] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    hint_confidence: HintConfidence | None = None


class SearchHit(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""
    domain: str = ""
    discovery_query: str = ""
    iteration: int = 1
    source_adapter: str = ""
    channel: str = "serp"
    page_evidence: PageEvidence | None = None


class SearchMeta(BaseModel):
    iterations_run: int = 0
    queries_used: list[str] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    engines_with_hits: list[str] = Field(default_factory=list)
    deep_read_count: int = 0
    message: str | None = None


class NexusSearchOptions(BaseModel):
    max_hits: int = 20
    max_deep_read: int = 10
    enable_iter2: bool = True
    max_iter1_queries: int = 5
    max_iter2_queries: int = 3
    max_domains_before_deep_read: int = 30
    proxy_url: str | None = None
    max_fetch_attempts: int = 2
    max_deep_read_seconds: float = 25.0
    allow_firecrawl: bool = True
    parallel_adapters: bool = False


class SearchBundle(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    meta: SearchMeta = Field(default_factory=SearchMeta)

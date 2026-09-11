"""Public facade: NexusSearchClient.search(query) -> SearchBundle."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import DiscoveryAdapter, discover_for_queries
from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import NexusSearchOptions, SearchBundle, SearchMeta
from nexusearch.page_reader import deep_read_hits
from nexusearch.profile import SearchProfile

logger = logging.getLogger(__name__)

_EMPTY_MSG = "No live search hits found."


class NexusSearchClient:
    """
    Multi-iteration live web discovery + optional deep page reading.

    Domain behavior comes exclusively from the required SearchProfile.
    Discovery backends are pluggable DiscoveryAdapters.
    """

    def __init__(
        self,
        *,
        profile: SearchProfile,
        tavily_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
        brave_api_key: str | None = None,
        serpapi_api_key: str | None = None,
        proxy_url: str | None = None,
        llm: LlmJsonClient | None = None,
        adapters: Sequence[DiscoveryAdapter] | None = None,
    ) -> None:
        if profile is None:
            raise TypeError("NexusSearchClient requires a SearchProfile")
        self.profile = profile
        self.tavily_api_key = tavily_api_key
        self.firecrawl_api_key = firecrawl_api_key
        self.brave_api_key = brave_api_key
        self.serpapi_api_key = serpapi_api_key
        self.proxy_url = proxy_url
        self.llm = llm
        self.adapters = self._filter_adapters(adapters)

    def _filter_adapters(
        self, adapters: Sequence[DiscoveryAdapter] | None
    ) -> Sequence[DiscoveryAdapter] | None:
        if adapters is None:
            return None
        channels = tuple(getattr(self.profile, "channels", ()) or ())
        if not channels:
            return adapters
        return [a for a in adapters if getattr(a, "channel", "serp") in channels]

    @classmethod
    def from_env(
        cls,
        *,
        profile: SearchProfile,
        llm: LlmJsonClient | None = None,
        settings: NexusSearchSettings | None = None,
        adapters: Sequence[DiscoveryAdapter] | None = None,
    ) -> NexusSearchClient:
        s = settings or NexusSearchSettings.from_env()
        return cls(
            profile=profile,
            tavily_api_key=s.tavily_api_key,
            firecrawl_api_key=s.firecrawl_api_key,
            brave_api_key=s.brave_api_key,
            serpapi_api_key=s.serpapi_api_key,
            proxy_url=s.proxy_url,
            llm=llm,
            adapters=adapters,
        )

    def search(self, query: str, options: NexusSearchOptions | None = None) -> SearchBundle:
        opts = options or NexusSearchOptions()
        proxy = opts.proxy_url or self.proxy_url
        ignored = tuple(self.profile.ignored_domains)
        queries_used: list[str] = []
        engines: list[str] = []
        engines_with_hits: list[str] = []
        iterations_run = 0

        iter1 = self.profile.plan_iter1(
            query,
            llm=self.llm,
            max_queries=opts.max_iter1_queries,
        )
        queries_used.extend(iter1)
        hits, eng1, hit1 = discover_for_queries(
            iter1,
            adapters=self.adapters,
            tavily_api_key=self.tavily_api_key,
            brave_api_key=self.brave_api_key,
            serpapi_api_key=self.serpapi_api_key,
            proxy=proxy,
            iteration=1,
            max_hits=opts.max_hits,
            ignored_domains=ignored,
            parallel=opts.parallel_adapters,
        )
        for e in eng1:
            if e not in engines:
                engines.append(e)
        for e in hit1:
            if e not in engines_with_hits:
                engines_with_hits.append(e)
        iterations_run = 1

        if opts.enable_iter2 and hits:
            iter2 = self.profile.plan_iter2(
                hits,
                already_used=queries_used,
                llm=self.llm,
                max_queries=opts.max_iter2_queries,
            )
            if iter2:
                queries_used.extend(iter2)
                hits, eng2, hit2 = discover_for_queries(
                    iter2,
                    adapters=self.adapters,
                    tavily_api_key=self.tavily_api_key,
                    brave_api_key=self.brave_api_key,
                    serpapi_api_key=self.serpapi_api_key,
                    proxy=proxy,
                    iteration=2,
                    max_hits=opts.max_hits,
                    ignored_domains=ignored,
                    existing=hits,
                    parallel=opts.parallel_adapters,
                )
                for e in eng2:
                    if e not in engines:
                        engines.append(e)
                for e in hit2:
                    if e not in engines_with_hits:
                        engines_with_hits.append(e)
                iterations_run = 2

        hits = hits[: opts.max_hits]

        deep_read_count = 0
        if hits and opts.max_deep_read > 0:
            deep_budget = min(opts.max_deep_read, opts.max_domains_before_deep_read, len(hits))
            fc_key = self.firecrawl_api_key if opts.allow_firecrawl else None
            hits = deep_read_hits(
                hits,
                profile=self.profile,
                max_deep_read=deep_budget,
                firecrawl_api_key=fc_key,
                proxy=proxy,
                max_fetch_attempts=opts.max_fetch_attempts,
                max_deep_read_seconds=opts.max_deep_read_seconds,
                allow_firecrawl=opts.allow_firecrawl,
            )
            deep_read_count = sum(
                1 for h in hits if h.page_evidence is not None and h.page_evidence.pages
            )

        message = None
        if not hits:
            message = _EMPTY_MSG
            logger.info(
                "Nexusearch empty query=%s profile=%s engines=%s engines_with_hits=%s",
                query,
                getattr(self.profile, "name", "?"),
                engines,
                engines_with_hits,
            )

        meta = SearchMeta(
            iterations_run=iterations_run,
            queries_used=queries_used,
            engines=engines,
            engines_with_hits=engines_with_hits,
            deep_read_count=deep_read_count,
            message=message,
        )
        return SearchBundle(hits=hits, meta=meta)

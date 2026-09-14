"""Public facade: NexusSearchClient.search(query) -> SearchBundle."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import (
    DiscoveryAdapter,
    discover_for_queries,
    extract_domain,
    matches_ignored,
)
from nexusearch.hooks import SearchHooks, emit_hooks_sync
from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import NexusSearchOptions, SearchBundle, SearchHit, SearchMeta
from nexusearch.page_reader import deep_read_hits
from nexusearch.profile import SearchProfile

logger = logging.getLogger(__name__)

_EMPTY_MSG = "No live search hits found."


def _merge_unique(dst: list[str], src: list[str]) -> None:
    for item in src:
        if item not in dst:
            dst.append(item)


def _apply_profile_geo_filter(hits: list[SearchHit], profile: SearchProfile) -> list[SearchHit]:
    """Hard geo filter from optional profile attrs (duck-typed, both opt-in):

    - allowed_domains: keep only exact/subdomain matches (whitelist mode)
    - url_path_patterns: keep only hits whose URL path matches any pattern

    Note: url_path_patterns on raw SERP hits is strict (a homepage has no
    /wholesale in its path); the softer alternative is profile.deep_paths.
    """
    allowed = tuple(getattr(profile, "allowed_domains", ()) or ())
    patterns = tuple(getattr(profile, "url_path_patterns", ()) or ())
    if not allowed and not patterns:
        return hits
    compiled: list[re.Pattern[str]] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error:
            logger.warning("Skipping invalid url_path_pattern %r", p)
    out: list[SearchHit] = []
    for h in hits:
        domain = (h.domain or extract_domain(h.url)).lower()
        if allowed and not any(matches_ignored(domain, a) for a in allowed):
            continue
        if compiled:
            try:
                path = urlsplit(h.url).path.lower()
            except ValueError:
                continue
            if not any(rx.search(path) for rx in compiled):
                continue
        out.append(h)
    dropped = len(hits) - len(out)
    if dropped:
        logger.info(
            "Profile geo filter dropped %s hits (profile=%s)",
            dropped,
            getattr(profile, "name", "?"),
        )
    return out


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
        hooks: Sequence[SearchHooks] = (),
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
        self._hooks = tuple(hooks)

    def _filter_adapters(
        self, adapters: Sequence[DiscoveryAdapter] | None
    ) -> Sequence[DiscoveryAdapter] | None:
        if adapters is None:
            return None
        channels = tuple(getattr(self.profile, "channels", ("serp",)) or ())
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
        return cls(profile=profile, llm=llm, adapters=adapters, **s.as_client_kwargs())

    def search(self, query: str, options: NexusSearchOptions | None = None) -> SearchBundle:
        opts = options or NexusSearchOptions()
        proxy = opts.proxy_url or self.proxy_url
        ignored = tuple(self.profile.ignored_domains)
        adapters = self.adapters
        if adapters is None:
            from nexusearch.discovery import default_adapters

            adapters = self._filter_adapters(default_adapters(
                tavily_api_key=self.tavily_api_key,
                brave_api_key=self.brave_api_key,
                serpapi_api_key=self.serpapi_api_key,
                proxy=proxy,
            ))
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
        emit_hooks_sync(self._hooks, "on_queries_planned", list(iter1), iteration=1)
        hits, eng1, hit1 = self._discover(
            iter1, iteration=1, opts=opts, proxy=proxy, adapters=adapters, ignored=ignored
        )
        _merge_unique(engines, eng1)
        _merge_unique(engines_with_hits, hit1)
        iterations_run = 1

        if opts.enable_iter2 and hits and len(hits) < opts.max_hits:
            iter2 = self.profile.plan_iter2(
                hits,
                already_used=queries_used,
                llm=self.llm,
                max_queries=opts.max_iter2_queries,
            )
            if iter2:
                emit_hooks_sync(self._hooks, "on_queries_planned", list(iter2), iteration=2)
                queries_used.extend(iter2)
                hits, eng2, hit2 = self._discover(
                    iter2, iteration=2, opts=opts, proxy=proxy, adapters=adapters,
                    ignored=ignored, existing=hits,
                )
                _merge_unique(engines, eng2)
                _merge_unique(engines_with_hits, hit2)
                iterations_run = 2

        hits = hits[: opts.max_hits]
        hits = _apply_profile_geo_filter(hits, self.profile)

        deep_read_count = 0
        rejected_count = 0
        if hits and opts.deep_read and opts.max_deep_read > 0:
            deep_budget = min(opts.max_deep_read, opts.max_domains_before_deep_read, len(hits))
            fc_key = self.firecrawl_api_key if opts.allow_firecrawl else None
            hits, rejected_count = deep_read_hits(
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
            profile=getattr(self.profile, "name", "") or "",
            rejected_count=rejected_count,
            message=message,
        )
        return SearchBundle(hits=hits, meta=meta)

    def _discover(
        self,
        queries: list[str],
        *,
        iteration: int,
        opts: NexusSearchOptions,
        proxy: str | None,
        adapters: Sequence[DiscoveryAdapter],
        ignored: tuple[str, ...],
        existing: list[SearchHit] | None = None,
    ) -> tuple[list[SearchHit], list[str], list[str]]:
        return discover_for_queries(
            queries,
            adapters=adapters,
            tavily_api_key=self.tavily_api_key,
            brave_api_key=self.brave_api_key,
            serpapi_api_key=self.serpapi_api_key,
            proxy=proxy,
            iteration=iteration,
            max_hits=opts.max_hits,
            ignored_domains=ignored,
            existing=existing,
            parallel=opts.parallel_adapters,
        )

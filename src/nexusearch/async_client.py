"""Async facade.

AsyncNexusSearchClient runs the sync pipeline in a worker thread (built-in
adapters are sync) while exposing an async API; hooks let callers observe
pipeline stages (see nexusearch.hooks.SearchHooks).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from nexusearch.client import NexusSearchClient
from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import DiscoveryAdapter
from nexusearch.hooks import HookPipeline, SearchHooks
from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import NexusSearchOptions, SearchBundle
from nexusearch.profile import SearchProfile

logger = logging.getLogger(__name__)

__all__ = ["AsyncNexusSearchClient", "SearchHooks"]


class AsyncNexusSearchClient:
    """Async entry point; mirrors NexusSearchClient but awaitable."""

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
        self._sync = NexusSearchClient(
            profile=profile,
            tavily_api_key=tavily_api_key,
            firecrawl_api_key=firecrawl_api_key,
            brave_api_key=brave_api_key,
            serpapi_api_key=serpapi_api_key,
            proxy_url=proxy_url,
            llm=llm,
            adapters=adapters,
            hooks=hooks,
        )
        self._hooks = HookPipeline(hooks)

    @classmethod
    def from_env(
        cls,
        *,
        profile: SearchProfile,
        llm: LlmJsonClient | None = None,
        settings: NexusSearchSettings | None = None,
        adapters: Sequence[DiscoveryAdapter] | None = None,
        hooks: Sequence[SearchHooks] = (),
    ) -> AsyncNexusSearchClient:
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
            hooks=hooks,
        )

    async def search(self, query: str, options: NexusSearchOptions | None = None) -> SearchBundle:
        opts = options or NexusSearchOptions()
        started = time.perf_counter()
        await self._hooks.emit("on_search_start", query, opts)
        try:
            bundle = await asyncio.to_thread(self._sync.search, query, opts)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            await self._hooks.emit("on_search_error", e, elapsed_ms=elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        for hit in bundle.hits:
            await self._hooks.emit("on_hit_discovered", hit)
        await self._hooks.emit("on_search_end", bundle, elapsed_ms=elapsed_ms)
        return bundle

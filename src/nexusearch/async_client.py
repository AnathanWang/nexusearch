"""Async facade + hooks middleware.

AsyncNexusSearchClient runs the sync pipeline in a worker thread (built-in
adapters are sync) while exposing an async API; hooks let callers observe and
intercept pipeline stages without subclassing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from nexusearch.client import NexusSearchClient
from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import DiscoveryAdapter
from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import NexusSearchOptions, SearchBundle, SearchHit
from nexusearch.profile import SearchProfile

logger = logging.getLogger(__name__)


@runtime_checkable
class SearchHooks(Protocol):
    """Optional middleware callbacks around pipeline stages (sync or async)."""

    def on_search_start(self, query: str, options: NexusSearchOptions) -> None: ...
    def on_queries_planned(self, queries: list[str], *, iteration: int) -> None: ...
    def on_hit_discovered(self, hit: SearchHit) -> None: ...
    def on_search_end(self, bundle: SearchBundle, *, elapsed_ms: float) -> None: ...


async def _maybe_await(value):  # noqa: ANN001, ANN202
    if asyncio.iscoroutine(value):
        return await value
    return value


class HookPipeline:
    """Best-effort hook dispatch; hook errors are logged, never raised."""

    def __init__(self, hooks: Sequence[SearchHooks] = ()) -> None:
        self.hooks = list(hooks)

    async def emit(self, method: str, *args, **kwargs) -> None:
        for hook in self.hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            try:
                await _maybe_await(fn(*args, **kwargs))
            except Exception:  # noqa: BLE001
                logger.exception("Search hook %s.%s failed", type(hook).__name__, method)


class AsyncNexusSearchClient:
    """Async entry point; mirrors NexusSearchClient but awaitable."""

    def __init__(
        self,
        *,
        profile: SearchProfile,
        tavily_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
        proxy_url: str | None = None,
        llm: LlmJsonClient | None = None,
        adapters: Sequence[DiscoveryAdapter] | None = None,
        hooks: Sequence[SearchHooks] = (),
    ) -> None:
        self._sync = NexusSearchClient(
            profile=profile,
            tavily_api_key=tavily_api_key,
            firecrawl_api_key=firecrawl_api_key,
            proxy_url=proxy_url,
            llm=llm,
            adapters=adapters,
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
            proxy_url=s.proxy_url,
            llm=llm,
            adapters=adapters,
            hooks=hooks,
        )

    async def search(self, query: str, options: NexusSearchOptions | None = None) -> SearchBundle:
        opts = options or NexusSearchOptions()
        started = time.perf_counter()
        await self._hooks.emit("on_search_start", query, opts)
        bundle = await asyncio.to_thread(self._sync.search, query, opts)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        for hit in bundle.hits:
            await self._hooks.emit("on_hit_discovered", hit)
        await self._hooks.emit("on_search_end", bundle, elapsed_ms=elapsed_ms)
        return bundle

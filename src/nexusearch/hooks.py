"""Search hooks middleware: protocol + dispatch helpers (sync & async)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from nexusearch.models import NexusSearchOptions, SearchBundle, SearchHit

logger = logging.getLogger(__name__)


@runtime_checkable
class SearchHooks(Protocol):
    """Optional middleware callbacks around pipeline stages (sync or async)."""

    def on_search_start(self, query: str, options: NexusSearchOptions) -> None: ...
    def on_queries_planned(self, queries: list[str], *, iteration: int) -> None: ...
    def on_hit_discovered(self, hit: SearchHit) -> None: ...
    def on_search_end(self, bundle: SearchBundle, *, elapsed_ms: float) -> None: ...
    def on_search_error(self, error: BaseException, *, elapsed_ms: float) -> None: ...


async def maybe_await(value):
    if inspect.isawaitable(value):
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
                await maybe_await(fn(*args, **kwargs))
            except Exception:
                logger.exception("Search hook %s.%s failed", type(hook).__name__, method)


def emit_hooks_sync(hooks: Sequence[SearchHooks], method: str, *args, **kwargs) -> None:
    """Sync dispatch for the sync pipeline. Awaitable hook results run via
    asyncio.run when no loop is active in this thread; skipped (with a warning)
    inside a running loop — use AsyncNexusSearchClient there."""
    for hook in hooks:
        fn = getattr(hook, method, None)
        if fn is None:
            continue
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    result.close() if inspect.iscoroutine(result) else None
                    logger.warning(
                        "Async hook %s.%s skipped inside a running event loop; "
                        "use AsyncNexusSearchClient for async hooks",
                        type(hook).__name__,
                        method,
                    )
        except Exception:
            logger.exception("Search hook %s.%s failed", type(hook).__name__, method)

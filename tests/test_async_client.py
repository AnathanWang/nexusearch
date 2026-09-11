"""Async client + hooks middleware tests."""

import asyncio

from nexusearch.async_client import AsyncNexusSearchClient
from nexusearch.models import NexusSearchOptions, SearchHit

from test_adapters import _Profile, _StubAdapter


class _Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def on_search_start(self, query, options):
        self.events.append(f"start:{query}")

    async def on_hit_discovered(self, hit: SearchHit):
        self.events.append(f"hit:{hit.domain}")

    def on_search_end(self, bundle, *, elapsed_ms: float):
        self.events.append(f"end:{len(bundle.hits)}:{elapsed_ms >= 0}")


class _BadHook:
    def on_search_start(self, query, options):
        raise RuntimeError("boom")


def test_async_client_runs_sync_pipeline():
    stub = _StubAdapter()
    client = AsyncNexusSearchClient(profile=_Profile(), adapters=[stub])
    bundle = asyncio.run(client.search("cnc machining", NexusSearchOptions(deep_read=False)))
    assert len(bundle.hits) == 1
    assert bundle.hits[0].source_adapter == "stub"


def test_hooks_receive_events_in_order():
    rec = _Recorder()
    stub = _StubAdapter()
    client = AsyncNexusSearchClient(profile=_Profile(), adapters=[stub], hooks=[rec])
    asyncio.run(client.search("cnc machining", NexusSearchOptions(deep_read=False)))
    assert rec.events[0] == "start:cnc machining"
    assert any(e.startswith("hit:cncmachining.example.com") for e in rec.events)
    assert rec.events[-1].startswith("end:1:")


def test_failing_hook_does_not_break_search():
    stub = _StubAdapter()
    client = AsyncNexusSearchClient(profile=_Profile(), adapters=[stub], hooks=[_BadHook()])
    bundle = asyncio.run(client.search("x", NexusSearchOptions(deep_read=False)))
    assert len(bundle.hits) == 1

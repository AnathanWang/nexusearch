"""Universal multi-iteration web search (profile-driven, no domain defaults)."""

from nexusearch.adapters_llm import LlmGroundedAdapter
from nexusearch.async_client import AsyncNexusSearchClient
from nexusearch.client import NexusSearchClient
from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import (
    BraveAdapter,
    DiscoveryAdapter,
    DuckDuckGoAdapter,
    SerpApiAdapter,
    TavilyAdapter,
    default_adapters,
)
from nexusearch.hooks import HookPipeline, SearchHooks, emit_hooks_sync
from nexusearch.llm_protocol import LlmJsonClient
from nexusearch.models import (
    HintConfidence,
    NexusSearchOptions,
    PageEvidence,
    PageSnippet,
    SearchBundle,
    SearchHit,
    SearchMeta,
)
from nexusearch.ops import Budget, SearchCache
from nexusearch.profile import SearchProfile

__all__ = [
    "AsyncNexusSearchClient",
    "BraveAdapter",
    "Budget",
    "DiscoveryAdapter",
    "DuckDuckGoAdapter",
    "HintConfidence",
    "HookPipeline",
    "LlmGroundedAdapter",
    "LlmJsonClient",
    "NexusSearchClient",
    "NexusSearchOptions",
    "NexusSearchSettings",
    "PageEvidence",
    "PageSnippet",
    "SearchBundle",
    "SearchCache",
    "SearchHit",
    "SearchHooks",
    "SearchMeta",
    "SearchProfile",
    "SerpApiAdapter",
    "TavilyAdapter",
    "default_adapters",
    "emit_hooks_sync",
]

__version__ = "0.3.0"

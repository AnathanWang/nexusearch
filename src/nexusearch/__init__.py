"""Universal multi-iteration web search (profile-driven, no domain defaults)."""

from nexusearch.adapters_expo import ExpoAdapter
from nexusearch.adapters_llm import LlmGroundedAdapter
from nexusearch.async_client import AsyncNexusSearchClient, SearchHooks
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
from nexusearch.profile import SearchProfile

__all__ = [
    "AsyncNexusSearchClient",
    "BraveAdapter",
    "DiscoveryAdapter",
    "DuckDuckGoAdapter",
    "ExpoAdapter",
    "HintConfidence",
    "LlmGroundedAdapter",
    "LlmJsonClient",
    "NexusSearchClient",
    "NexusSearchOptions",
    "NexusSearchSettings",
    "PageEvidence",
    "PageSnippet",
    "SearchBundle",
    "SearchHit",
    "SearchHooks",
    "SearchMeta",
    "SearchProfile",
    "SerpApiAdapter",
    "TavilyAdapter",
    "default_adapters",
]

__version__ = "0.1.0"

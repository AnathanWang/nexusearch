"""Universal multi-iteration web search (profile-driven, no domain defaults)."""

from nexusearch.client import NexusSearchClient
from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import (
    DiscoveryAdapter,
    DuckDuckGoAdapter,
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
    "DiscoveryAdapter",
    "DuckDuckGoAdapter",
    "HintConfidence",
    "LlmJsonClient",
    "NexusSearchClient",
    "NexusSearchOptions",
    "NexusSearchSettings",
    "PageEvidence",
    "PageSnippet",
    "SearchBundle",
    "SearchHit",
    "SearchMeta",
    "SearchProfile",
    "TavilyAdapter",
    "default_adapters",
]

__version__ = "0.1.0"

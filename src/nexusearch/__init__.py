"""Universal multi-iteration web search (profile-driven, no domain defaults)."""

from nexusearch.client import NexusSearchClient
from nexusearch.config import NexusSearchSettings
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
]

__version__ = "0.1.0"

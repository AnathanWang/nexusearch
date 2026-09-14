"""Optional settings helpers for nexusearch.

Keys are ALWAYS owned by the consumer project: pass them explicitly to
NexusSearchClient / AsyncNexusSearchClient / RoutedSearchClient. This helper
is only a convenience for consumers who keep keys in their own environment;
the library never reads env vars on its own and knows no consumer-specific
variable names.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class NexusSearchSettings:
    tavily_api_key: str | None = None
    firecrawl_api_key: str | None = None
    brave_api_key: str | None = None
    serpapi_api_key: str | None = None
    proxy_url: str | None = None

    @classmethod
    def from_env(cls) -> NexusSearchSettings:
        """Read provider-standard / nexusearch-generic env names.

        Precedence for proxy: NEXUSEARCH_PROXY_URL only (consumer-specific
        names like S3_SCOUT_PROXY_URL belong to the consumer's own settings
        and must be passed explicitly via proxy_url=...).
        """
        return cls(
            tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
            firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY") or None,
            brave_api_key=os.getenv("BRAVE_API_KEY") or None,
            serpapi_api_key=os.getenv("SERPAPI_API_KEY") or None,
            proxy_url=os.getenv("NEXUSEARCH_PROXY_URL") or None,
        )

    def as_client_kwargs(self) -> dict[str, str | None]:
        """Constructor kwargs shared by NexusSearchClient / AsyncNexusSearchClient."""
        return {
            "tavily_api_key": self.tavily_api_key,
            "firecrawl_api_key": self.firecrawl_api_key,
            "brave_api_key": self.brave_api_key,
            "serpapi_api_key": self.serpapi_api_key,
            "proxy_url": self.proxy_url,
        }

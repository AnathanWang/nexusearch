"""Optional settings helpers for nexusearch.

Proxy env precedence: NEXUSEARCH_PROXY_URL, then legacy fallbacks
DEEP_SEARCH_PROXY_URL / S3_SCOUT_PROXY_URL (deprecated; will be dropped in 0.3.0).
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
        return cls(
            tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
            firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY") or None,
            brave_api_key=os.getenv("BRAVE_API_KEY") or None,
            serpapi_api_key=os.getenv("SERPAPI_API_KEY") or None,
            proxy_url=(
                os.getenv("NEXUSEARCH_PROXY_URL")
                or os.getenv("DEEP_SEARCH_PROXY_URL")
                or os.getenv("S3_SCOUT_PROXY_URL")
                or None
            ),
        )

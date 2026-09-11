"""Expo / trade-show discovery channel.

Finds trade shows and exhibitor-list pages for a query. Uses any SERP-capable
adapter as backend (defaults to DDG) but restricts queries to expo terms and
keeps only hits on known expo/trade-show domains or URLs with expo-ish paths.
Exhibitor company domains themselves come from later site expansion (Phase C/D),
not invented here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from urllib.parse import urlsplit

from nexusearch.discovery import DiscoveryAdapter, DuckDuckGoAdapter, extract_domain
from nexusearch.models import SearchHit

logger = logging.getLogger(__name__)

# Well-known trade-show / expo platforms (extend per deployment).
DEFAULT_EXPO_DOMAINS: tuple[str, ...] = (
    "10times.com",
    "expodatabase.com",
    "tradefairdates.com",
    "eventseye.com",
    "exhibitoronline.com",
    "tsnn.com",
    "nfer.com",
    "messefrankfurt.com",
    "informa.com",
    "clarionevents.com",
    "rxglobal.com",
    "expomap.com",
    "expocentr.ru",
    "exponet.ru",
)

_EXPO_PATH_HINTS = ("exhibitor", "expo", "trade-show", "tradeshow", "fair", "messe", "vystavka")


class ExpoAdapter:
    """Discover trade-show pages for a query via a SERP backend."""

    name = "expo"
    channel = "expo"

    def __init__(
        self,
        backend: DiscoveryAdapter | None = None,
        *,
        expo_domains: Sequence[str] = DEFAULT_EXPO_DOMAINS,
        proxy: str | None = None,
    ) -> None:
        self.backend = backend or DuckDuckGoAdapter(proxy=proxy)
        self.expo_domains = tuple(d.lower() for d in expo_domains)

    def _is_expo_hit(self, url: str, domain: str) -> bool:
        if any(domain == d or domain.endswith("." + d) for d in self.expo_domains):
            return True
        path = urlsplit(url).path.lower()
        return any(hint in path for hint in _EXPO_PATH_HINTS)

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        expo_query = f"{query} trade show exhibitors list"
        raw_hits, used = self.backend.discover(
            expo_query,
            max_results=max_results * 3,
            iteration=iteration,
            ignored_domains=ignored_domains,
        )
        if not used:
            return [], False
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for h in raw_hits:
            domain = h.domain or extract_domain(h.url)
            if not domain or domain in seen:
                continue
            if not self._is_expo_hit(h.url, domain):
                continue
            seen.add(domain)
            hits.append(
                h.model_copy(update={
                    "discovery_query": expo_query,
                    "source_adapter": self.name,
                    "channel": self.channel,
                })
            )
            if len(hits) >= max_results:
                break
        return hits, True

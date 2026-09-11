"""Declarative domain-channel adapter: custom discovery channels without classes.

A DomainChannelAdapter wraps any SERP-capable backend adapter with consumer
query templates, then keeps only hits matching a domain whitelist and/or URL
path patterns. Domain data (domains, patterns, templates) is supplied by the
consumer; the library provides only the mechanics.

Example (trade-show channel):

    DomainChannelAdapter(
        name="expo",
        query_templates=["{query} trade show exhibitors list"],
        allowed_domains=["10times.com", "expocentr.ru"],
        path_patterns=[r"(?:^|[/._-])(expos?|exhibitors?)(?:[/._-]|$)"],
    )
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from nexusearch.discovery import DiscoveryAdapter, DuckDuckGoAdapter, extract_domain
from nexusearch.models import SearchHit

logger = logging.getLogger(__name__)


class DomainChannelAdapter:
    """Declarative discovery channel implementing the DiscoveryAdapter protocol."""

    def __init__(
        self,
        *,
        name: str,
        query_templates: Sequence[str],
        channel: str | None = None,
        allowed_domains: Sequence[str] = (),
        path_patterns: Sequence[str] = (),
        backend: DiscoveryAdapter | None = None,
        proxy: str | None = None,
        oversample: int = 3,
    ) -> None:
        if not name or not name.strip():
            raise ValueError("DomainChannelAdapter requires a non-empty name")
        if not query_templates:
            raise ValueError("DomainChannelAdapter requires at least one query template")
        for t in query_templates:
            if "{query}" not in t:
                raise ValueError(f"query template must contain '{{query}}': {t!r}")
        if not allowed_domains and not path_patterns:
            raise ValueError(
                "DomainChannelAdapter requires allowed_domains and/or path_patterns "
                "(without any filter the channel is meaningless)"
            )
        if oversample < 1:
            raise ValueError("oversample must be >= 1")
        self.name = name.strip()
        self.channel = (channel or self.name).strip() or self.name
        self.query_templates = tuple(query_templates)
        self.allowed_domains = tuple(d.lower().strip() for d in allowed_domains if d.strip())
        self.path_patterns = tuple(re.compile(p, re.IGNORECASE) for p in path_patterns)
        self.backend = backend or DuckDuckGoAdapter(proxy=proxy)
        self.oversample = oversample

    def _matches(self, url: str, domain: str) -> bool:
        if any(domain == d or domain.endswith("." + d) for d in self.allowed_domains):
            return True
        try:
            path = urlsplit(url).path.lower()
        except ValueError:
            return False
        return any(rx.search(path) for rx in self.path_patterns)

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        hits: list[SearchHit] = []
        seen: set[str] = set()
        attempted_ok = False
        for template in self.query_templates:
            if len(hits) >= max_results:
                break
            # replace() instead of .format(): templates may contain other braces.
            channel_query = template.replace("{query}", query)
            raw_hits, used = self.backend.discover(
                channel_query,
                max_results=max_results * self.oversample,
                iteration=iteration,
                ignored_domains=ignored_domains,
            )
            attempted_ok = attempted_ok or used
            for h in raw_hits:
                domain = (h.domain or extract_domain(h.url)).lower()
                if not domain or domain in seen:
                    continue
                if not self._matches(h.url, domain):
                    continue
                seen.add(domain)
                hits.append(
                    h.model_copy(update={
                        "domain": domain,
                        "discovery_query": channel_query,
                        "source_adapter": self.name,
                        "channel": self.channel,
                    })
                )
                if len(hits) >= max_results:
                    break
        return hits, attempted_ok

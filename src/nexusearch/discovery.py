"""Discovery adapters (engines / channels) and their merge orchestration."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from nexusearch.http_util import request_with_retries
from nexusearch.models import SearchHit

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None


# --- domain utils -----------------------------------------------------------

def extract_domain(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or parsed.path.split("/")[0]
        return (host or "").replace("www.", "").lower().strip()
    except (ValueError, AttributeError):
        return url.split("//")[-1].split("/")[0].replace("www.", "").lower().strip()


def _matches_ignored(domain: str, ign: str) -> bool:
    """
    Match ignored patterns without substring false positives.
    - Trailing '.' (e.g. 'amazon.') → prefix / label match (amazon.com, amazon.co.uk)
    - Otherwise exact host or subdomain (x.com, foo.x.com) — not prefixx.com
    """
    d = domain.lower().rstrip(".")
    raw = ign.lower()
    if raw.endswith("."):
        label = raw.rstrip(".")
        return d == label or d.startswith(label + ".")
    return d == raw or d.endswith("." + raw)


def is_allowed_domain(
    domain: str,
    seen: set[str],
    ignored: Sequence[str] = (),
) -> bool:
    if not domain or domain in seen:
        return False
    return not any(_matches_ignored(domain, ign) for ign in ignored)


def merge_hits(
    existing: list[SearchHit],
    new_hits: list[SearchHit],
    *,
    max_hits: int,
    ignored_domains: Sequence[str] = (),
) -> list[SearchHit]:
    seen = {h.domain for h in existing if h.domain}
    out = list(existing)
    for h in new_hits:
        if not h.domain or not is_allowed_domain(h.domain, seen, ignored_domains):
            continue
        seen.add(h.domain)
        out.append(h)
        if len(out) >= max_hits:
            break
    return out


# --- Adapter protocol -------------------------------------------------------

@runtime_checkable
class DiscoveryAdapter(Protocol):
    """One discovery backend (SERP engine, grounded LLM, expo, directory…)."""

    name: str
    channel: str

    def discover(
        self,
        query: str,
        *,
        max_results: int,
        iteration: int,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        """Return (hits, attempted_ok). attempted_ok only after a successful response."""
        ...


# --- Built-in adapters ------------------------------------------------------

class TavilyAdapter:
    name = "tavily"
    channel = "serp"

    def __init__(self, api_key: str | None, *, attempts: int = 3) -> None:
        self.api_key = api_key
        self.attempts = attempts

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        if not self.api_key or TavilyClient is None:
            return [], False

        last_err: Exception | None = None
        search_res: dict | None = None
        for i in range(max(1, self.attempts)):
            try:
                client = TavilyClient(api_key=self.api_key)
                search_res = client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=max_results,
                    include_domains=[],
                )
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if i >= self.attempts - 1:
                    logger.warning("Tavily search failed for '%s': %s", query, e)
                    return [], False
                delay = 0.35 * (2**i)
                logger.debug("Tavily retry %s/%s: %s", i + 1, self.attempts, e)
                time.sleep(delay)

        if not isinstance(search_res, dict):
            logger.warning("Tavily search failed for '%s': %s", query, last_err)
            return [], False

        hits: list[SearchHit] = []
        for item in search_res.get("results", []):
            url = item.get("url", "") or ""
            domain = extract_domain(url)
            if not domain:
                continue
            hits.append(
                SearchHit(
                    title=item.get("title", "") or "",
                    url=url,
                    snippet=item.get("content", "") or "",
                    domain=domain,
                    discovery_query=query,
                    iteration=iteration,
                    source_adapter=self.name,
                    channel=self.channel,
                )
            )
        return hits, True


def _parse_ddg_html_items(html_text: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[dict[str, str]] = []
    for r in soup.select(".result__body"):
        t_el = r.select_one(".result__title a")
        s_el = r.select_one(".result__snippet")
        if not t_el:
            continue
        raw_href = t_el.get("href", "")
        target_url = raw_href
        if isinstance(raw_href, str) and "uddg=" in raw_href:
            match = re.search(r"uddg=([^&]+)", raw_href)
            if match:
                target_url = unquote(match.group(1))
        if not isinstance(target_url, str) or not target_url.startswith("http"):
            continue
        items.append({
            "title": t_el.get_text(strip=True),
            "url": target_url,
            "snippet": s_el.get_text(strip=True) if s_el else "",
        })
    return items


class DuckDuckGoAdapter:
    name = "ddg"
    channel = "serp"

    def __init__(self, proxy: str | None = None) -> None:
        self.proxy = proxy

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
        used = False

        if DDGS is not None:
            try:
                with DDGS(proxy=self.proxy) if self.proxy else DDGS() as ddgs:
                    try:
                        items = list(ddgs.text(query, max_results=max_results + 2))
                        used = True
                    except Exception as e:  # noqa: BLE001
                        logger.debug("DDGS text error for '%s': %s", query, e)
                        items = []
                    for item in items:
                        url = item.get("href") or item.get("link") or ""
                        if not url.startswith("http"):
                            continue
                        domain = extract_domain(url)
                        if not is_allowed_domain(domain, seen, ignored_domains):
                            continue
                        seen.add(domain)
                        hits.append(
                            SearchHit(
                                title=item.get("title", "") or "",
                                url=url,
                                snippet=item.get("body", "") or "",
                                domain=domain,
                                discovery_query=query,
                                iteration=iteration,
                                source_adapter=self.name,
                                channel=self.channel,
                            )
                        )
                        if len(hits) >= max_results:
                            return hits, used
            except Exception as e:  # noqa: BLE001
                logger.warning("DDGS session failed: %s", e)

        if len(hits) >= max_results:
            return hits, used

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            with httpx.Client(proxy=self.proxy, timeout=8.0, follow_redirects=True) as client:
                resp = request_with_retries(
                    client,
                    "GET",
                    "https://html.duckduckgo.com/html/",
                    attempts=3,
                    params={"q": query},
                    headers=headers,
                )
                if resp.status_code == 200:
                    used = True
                    for item in _parse_ddg_html_items(resp.text):
                        domain = extract_domain(item["url"])
                        if not is_allowed_domain(domain, seen, ignored_domains):
                            continue
                        seen.add(domain)
                        hits.append(
                            SearchHit(
                                title=item["title"],
                                url=item["url"],
                                snippet=item["snippet"],
                                domain=domain,
                                discovery_query=query,
                                iteration=iteration,
                                source_adapter=self.name,
                                channel=self.channel,
                            )
                        )
                        if len(hits) >= max_results:
                            break
        except Exception as e:  # noqa: BLE001
            logger.debug("DDG HTML fallback failed for '%s': %s", query, e)

        return hits, used


def default_adapters(
    *,
    tavily_api_key: str | None = None,
    proxy: str | None = None,
) -> list[DiscoveryAdapter]:
    adapters: list[DiscoveryAdapter] = []
    if tavily_api_key and TavilyClient is not None:
        adapters.append(TavilyAdapter(tavily_api_key))
    adapters.append(DuckDuckGoAdapter(proxy=proxy))
    return adapters


# --- Orchestration ----------------------------------------------------------

def discover_for_queries(
    queries: list[str],
    *,
    adapters: Sequence[DiscoveryAdapter] | None = None,
    tavily_api_key: str | None = None,
    proxy: str | None = None,
    iteration: int,
    max_hits: int,
    ignored_domains: Sequence[str] = (),
    existing: list[SearchHit] | None = None,
) -> tuple[list[SearchHit], list[str], list[str]]:
    """
    Run discovery for each query over adapters (first fills, rest fill gaps).

    Returns (hits, adapters_attempted, adapters_with_hits).
    """
    hits = list(existing or [])
    engines: list[str] = []
    engines_with_hits: list[str] = []
    per_query = max(3, max_hits // max(len(queries), 1) + 2)
    active: Sequence[DiscoveryAdapter] = adapters if adapters is not None else default_adapters(
        tavily_api_key=tavily_api_key,
        proxy=proxy,
    )

    for q in queries:
        if len(hits) >= max_hits:
            break
        for adapter in active:
            if len(hits) >= max_hits:
                break
            needed = max_hits - len(hits)
            before = len(hits)
            a_hits, used = adapter.discover(
                q,
                max_results=min(per_query, needed),
                iteration=iteration,
                ignored_domains=ignored_domains,
            )
            if used and adapter.name not in engines:
                engines.append(adapter.name)
            hits = merge_hits(hits, a_hits, max_hits=max_hits, ignored_domains=ignored_domains)
            if len(hits) > before and adapter.name not in engines_with_hits:
                engines_with_hits.append(adapter.name)

    return hits, engines, engines_with_hits

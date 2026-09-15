"""Discovery adapters (engines / channels) and their merge orchestration."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from nexusearch.circuit import CircuitBreaker
from nexusearch.http_util import backoff_delay, request_with_retries
from nexusearch.models import SearchHit
from nexusearch.ratelimit import RateLimiter

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

_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# --- domain utils -----------------------------------------------------------

def extract_domain(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or parsed.path.split("/")[0]
        return (host or "").removeprefix("www.").lower().strip()
    except (ValueError, AttributeError):
        return url.split("//")[-1].split("/")[0].removeprefix("www.").lower().strip()


def matches_ignored(domain: str, ign: str) -> bool:
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
    return not any(matches_ignored(domain, ign) for ign in ignored)


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
        if len(out) >= max_hits:
            break
        if not h.domain or not is_allowed_domain(h.domain, seen, ignored_domains):
            continue
        seen.add(h.domain)
        out.append(h)
    return out


# --- adapter internals ------------------------------------------------------

_CLIENT_INIT_LOCK = threading.Lock()


class _ReusableHttp:
    """Mixin: one lazily-created httpx.Client per adapter instance.

    httpx.Client is thread-safe, so sharing is safe under parallel_adapters.
    Call close() when the adapter is done (process teardown, tests).
    """

    _client: httpx.Client | None = None

    def _http(self, *, proxy: str | None, timeout: float) -> httpx.Client:
        if self._client is None:
            with _CLIENT_INIT_LOCK:
                if self._client is None:
                    self._client = httpx.Client(
                        proxy=proxy, timeout=timeout, follow_redirects=True
                    )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _rate_limit_ok(limiter: RateLimiter | None, adapter_name: str) -> bool:
    """Shared guard preamble: an exhausted limiter means 'not attempted'."""
    if limiter is None or limiter.acquire():
        return True
    logger.warning("%s rate limit exhausted, skipping call", adapter_name)
    return False


def _record_success(breaker: CircuitBreaker | None) -> None:
    if breaker is not None:
        breaker.record_success()


def _record_failure(breaker: CircuitBreaker | None) -> None:
    if breaker is not None:
        breaker.record_failure()


def _make_hit(
    adapter: DiscoveryAdapter,
    *,
    title: str,
    url: str,
    snippet: str,
    query: str,
    iteration: int,
) -> SearchHit | None:
    """Validate a raw SERP result and stamp provenance; None if unusable."""
    if not url.startswith("http"):
        return None
    domain = extract_domain(url)
    if not domain:
        return None
    return SearchHit(
        title=title,
        url=url,
        snippet=snippet,
        domain=domain,
        discovery_query=query,
        iteration=iteration,
        source_adapter=adapter.name,
        channel=adapter.channel,
    )


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

    _tavily: object | None = None  # lazily-created TavilyClient, shared across calls

    def __init__(
        self,
        api_key: str | None,
        *,
        attempts: int = 3,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.api_key = api_key
        self.attempts = attempts
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker

    def _tavily_client(self):
        if self._tavily is None:
            with _CLIENT_INIT_LOCK:
                if self._tavily is None:
                    self._tavily = TavilyClient(api_key=self.api_key)
        return self._tavily

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
        if not _rate_limit_ok(self.rate_limiter, "Tavily"):
            return [], False

        last_err: Exception | None = None
        search_res: dict | None = None
        for i in range(max(1, self.attempts)):
            try:
                search_res = self._tavily_client().search(
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
                    _record_failure(self.circuit_breaker)
                    return [], False
                logger.debug("Tavily retry %s/%s: %s", i + 1, self.attempts, e)
                time.sleep(backoff_delay(i))

        if not isinstance(search_res, dict):
            logger.warning("Tavily search failed for '%s': %s", query, last_err)
            return [], False

        _record_success(self.circuit_breaker)
        hits: list[SearchHit] = []
        for item in search_res.get("results") or []:
            if not isinstance(item, dict):
                continue
            hit = _make_hit(
                self,
                title=item.get("title", "") or "",
                url=item.get("url", "") or "",
                snippet=item.get("content", "") or "",
                query=query,
                iteration=iteration,
            )
            if hit is not None:
                hits.append(hit)
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
                # Unquote until stable: DDG sometimes double-encodes targets.
                prev = match.group(1)
                for _ in range(3):
                    decoded = unquote(prev)
                    if decoded == prev:
                        break
                    prev = decoded
                target_url = prev
        if not isinstance(target_url, str) or not target_url.startswith("http"):
            continue
        try:
            if not urlsplit(target_url).hostname:
                continue
        except ValueError:
            continue
        items.append({
            "title": t_el.get_text(strip=True),
            "url": target_url,
            "snippet": s_el.get_text(strip=True) if s_el else "",
        })
    return items


class DuckDuckGoAdapter(_ReusableHttp):
    """DDG discovery: ddgs library first, HTML scrape as fallback."""

    name = "ddg"
    channel = "serp"

    def __init__(
        self,
        proxy: str | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.proxy = proxy
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        if not _rate_limit_ok(self.rate_limiter, "DDG"):
            return [], False
        hits: list[SearchHit] = []
        seen: set[str] = set()
        used = self._via_ddgs(
            query, hits=hits, seen=seen,
            max_results=max_results, iteration=iteration, ignored_domains=ignored_domains,
        )
        if len(hits) < max_results:
            used = self._via_html(
                query, hits=hits, seen=seen,
                max_results=max_results, iteration=iteration, ignored_domains=ignored_domains,
            ) or used
        if used:
            _record_success(self.circuit_breaker)
        else:
            _record_failure(self.circuit_breaker)
        return hits, used

    def _append(
        self,
        hits: list[SearchHit],
        seen: set[str],
        *,
        title: str,
        url: str,
        snippet: str,
        query: str,
        iteration: int,
        max_results: int,
        ignored_domains: Sequence[str],
    ) -> None:
        if len(hits) >= max_results:
            return
        hit = _make_hit(self, title=title, url=url, snippet=snippet, query=query, iteration=iteration)
        if hit is None or not is_allowed_domain(hit.domain, seen, ignored_domains):
            return
        seen.add(hit.domain)
        hits.append(hit)

    def _via_ddgs(
        self,
        query: str,
        *,
        hits: list[SearchHit],
        seen: set[str],
        max_results: int,
        iteration: int,
        ignored_domains: Sequence[str],
    ) -> bool:
        """Library path; True only if the API actually responded."""
        if DDGS is None:
            return False
        try:
            with DDGS(proxy=self.proxy) if self.proxy else DDGS() as ddgs:
                try:
                    items = list(ddgs.text(query, max_results=max_results + 2))
                except Exception as e:  # noqa: BLE001
                    logger.debug("DDGS text error for '%s': %s", query, e)
                    return False
                for item in items:
                    self._append(
                        hits, seen,
                        title=item.get("title", "") or "",
                        url=item.get("href") or item.get("link") or "",
                        snippet=item.get("body", "") or "",
                        query=query, iteration=iteration,
                        max_results=max_results, ignored_domains=ignored_domains,
                    )
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("DDGS session failed: %s", e)
            return False

    def _via_html(
        self,
        query: str,
        *,
        hits: list[SearchHit],
        seen: set[str],
        max_results: int,
        iteration: int,
        ignored_domains: Sequence[str],
    ) -> bool:
        """Scrape path; True only on an HTTP 200 page."""
        try:
            resp = request_with_retries(
                self._http(proxy=self.proxy, timeout=8.0),
                "GET",
                "https://html.duckduckgo.com/html/",
                attempts=3,
                params={"q": query},
                headers=_DDG_HEADERS,
            )
            if resp.status_code != 200:
                return False
            for item in _parse_ddg_html_items(resp.text):
                self._append(
                    hits, seen,
                    title=item["title"],
                    url=item["url"],
                    snippet=item["snippet"],
                    query=query, iteration=iteration,
                    max_results=max_results, ignored_domains=ignored_domains,
                )
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("DDG HTML fallback failed for '%s': %s", query, e)
            return False


class BraveAdapter(_ReusableHttp):
    """Brave Search API adapter (BRAVE_API_KEY)."""

    name = "brave"
    channel = "serp"

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 8.0,
        proxy: str | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.proxy = proxy
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        if not self.api_key:
            return [], False
        if not _rate_limit_ok(self.rate_limiter, "Brave"):
            return [], False

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }
        try:
            resp = request_with_retries(
                self._http(proxy=self.proxy, timeout=self.timeout),
                "GET",
                "https://api.search.brave.com/res/v1/web/search",
                attempts=3,
                # Brave caps `count` at 20 per request.
                params={"q": query, "count": min(max_results, 20)},
                headers=headers,
            )
            if resp.status_code != 200:
                logger.debug("Brave search HTTP %s for '%s'", resp.status_code, query)
                return [], False
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("Brave search failed for '%s': %s", query, e)
            _record_failure(self.circuit_breaker)
            return [], False

        _record_success(self.circuit_breaker)
        hits: list[SearchHit] = []
        for item in (data.get("web") or {}).get("results", []):
            if not isinstance(item, dict):
                continue
            hit = _make_hit(
                self,
                title=item.get("title", "") or "",
                url=item.get("url", "") or "",
                snippet=item.get("description", "") or "",
                query=query,
                iteration=iteration,
            )
            if hit is not None:
                hits.append(hit)
        return hits, True


class SerpApiAdapter(_ReusableHttp):
    """SerpAPI Google adapter (SERPAPI_API_KEY).

    Note: SerpAPI requires the key in the query string; httpx logs full request
    URLs at INFO, so we pin the httpx logger to WARNING to avoid key leakage.
    """

    name = "serpapi"
    channel = "serp"

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 10.0,
        proxy: str | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.proxy = proxy
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker
        logging.getLogger("httpx").setLevel(logging.WARNING)

    def discover(
        self,
        query: str,
        *,
        max_results: int = 10,
        iteration: int = 1,
        ignored_domains: Sequence[str] = (),
    ) -> tuple[list[SearchHit], bool]:
        if not self.api_key:
            return [], False
        if not _rate_limit_ok(self.rate_limiter, "SerpAPI"):
            return [], False

        params = {
            "engine": "google",
            "q": query,
            "num": max_results,
            "api_key": self.api_key,
        }
        try:
            resp = request_with_retries(
                self._http(proxy=self.proxy, timeout=self.timeout),
                "GET", "https://serpapi.com/search.json", attempts=3, params=params
            )
            if resp.status_code != 200:
                logger.debug("SerpAPI HTTP %s for '%s'", resp.status_code, query)
                return [], False
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("SerpAPI search failed for '%s': %s", query, e)
            _record_failure(self.circuit_breaker)
            return [], False

        _record_success(self.circuit_breaker)
        hits: list[SearchHit] = []
        for item in data.get("organic_results", []):
            if not isinstance(item, dict):
                continue
            hit = _make_hit(
                self,
                title=item.get("title", "") or "",
                url=item.get("link", "") or "",
                snippet=item.get("snippet", "") or "",
                query=query,
                iteration=iteration,
            )
            if hit is not None:
                hits.append(hit)
        return hits, True


def default_adapters(
    *,
    tavily_api_key: str | None = None,
    brave_api_key: str | None = None,
    serpapi_api_key: str | None = None,
    proxy: str | None = None,
) -> list[DiscoveryAdapter]:
    adapters: list[DiscoveryAdapter] = []
    if tavily_api_key and TavilyClient is not None:
        adapters.append(TavilyAdapter(tavily_api_key))
    if brave_api_key:
        adapters.append(BraveAdapter(brave_api_key, proxy=proxy))
    if serpapi_api_key:
        adapters.append(SerpApiAdapter(serpapi_api_key, proxy=proxy))
    adapters.append(DuckDuckGoAdapter(proxy=proxy))
    return adapters


# --- Orchestration ----------------------------------------------------------

def discover_for_queries(
    queries: list[str],
    *,
    adapters: Sequence[DiscoveryAdapter] | None = None,
    tavily_api_key: str | None = None,
    brave_api_key: str | None = None,
    serpapi_api_key: str | None = None,
    proxy: str | None = None,
    iteration: int,
    max_hits: int,
    ignored_domains: Sequence[str] = (),
    existing: list[SearchHit] | None = None,
    parallel: bool = False,
) -> tuple[list[SearchHit], list[str], list[str]]:
    """
    Run discovery for each query over adapters (first fills, rest fill gaps).

    With parallel=True, adapters for one query run concurrently in threads and
    their hits are merged by first-appearance rank across adapters.

    Returns (hits, adapters_attempted, adapters_with_hits).
    """
    hits = list(existing or [])
    engines: list[str] = []
    engines_with_hits: list[str] = []
    per_query = max(3, max_hits // max(len(queries), 1) + 2)
    active: Sequence[DiscoveryAdapter] = adapters if adapters is not None else default_adapters(
        tavily_api_key=tavily_api_key,
        brave_api_key=brave_api_key,
        serpapi_api_key=serpapi_api_key,
        proxy=proxy,
    )

    def _call_adapter(adapter: DiscoveryAdapter, q: str, limit: int) -> tuple[str, list[SearchHit], bool]:
        """Isolated adapter call: a crashing adapter degrades to ([], False)."""
        try:
            a_hits, used = adapter.discover(
                q, max_results=limit, iteration=iteration, ignored_domains=ignored_domains
            )
        except Exception:
            logger.exception("Discovery adapter %s crashed on query %r", adapter.name, q)
            return adapter.name, [], False
        return adapter.name, a_hits, used

    def _absorb(name: str, a_hits: list[SearchHit], used: bool) -> None:
        if used and name not in engines:
            engines.append(name)
        before = len(hits)
        hits[:] = merge_hits(hits, a_hits, max_hits=max_hits, ignored_domains=ignored_domains)
        if len(hits) > before and name not in engines_with_hits:
            engines_with_hits.append(name)

    if parallel and len(active) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            for q in queries:
                if len(hits) >= max_hits:
                    break
                needed = max_hits - len(hits)
                limit = min(per_query, needed)
                results = list(pool.map(
                    lambda a, q=q, limit=limit: _call_adapter(a, q, limit),
                    active,
                ))
                for name, a_hits, used in results:
                    if len(hits) >= max_hits:
                        break
                    _absorb(name, a_hits, used)
        return hits, engines, engines_with_hits

    for q in queries:
        if len(hits) >= max_hits:
            break
        for adapter in active:
            if len(hits) >= max_hits:
                break
            needed = max_hits - len(hits)
            _name, a_hits, used = _call_adapter(adapter, q, min(per_query, needed))
            _absorb(_name, a_hits, used)

    return hits, engines, engines_with_hits

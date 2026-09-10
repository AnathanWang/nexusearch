"""Live discovery adapters: Tavily (primary) + DuckDuckGo (secondary)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
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


def search_tavily(
    query: str,
    *,
    api_key: str | None,
    max_results: int = 10,
    iteration: int = 1,
    attempts: int = 3,
) -> tuple[list[SearchHit], bool]:
    """Returns (hits, engine_used). engine_used only after a successful API response."""
    if not api_key or TavilyClient is None:
        return [], False

    last_err: Exception | None = None
    search_res: dict | None = None
    for i in range(max(1, attempts)):
        try:
            client = TavilyClient(api_key=api_key)
            search_res = client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
                include_domains=[],
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i >= attempts - 1:
                logger.warning("Tavily search failed for '%s': %s", query, e)
                return [], False
            delay = 0.35 * (2**i)
            logger.debug("Tavily retry %s/%s: %s", i + 1, attempts, e)
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


def search_ddg(
    query: str,
    *,
    max_results: int = 10,
    proxy: str | None = None,
    iteration: int = 1,
    ignored_domains: Sequence[str] = (),
) -> tuple[list[SearchHit], bool]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    used = False

    if DDGS is not None:
        try:
            with DDGS(proxy=proxy) if proxy else DDGS() as ddgs:
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
        with httpx.Client(proxy=proxy, timeout=8.0, follow_redirects=True) as client:
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
                        )
                    )
                    if len(hits) >= max_results:
                        break
    except Exception as e:  # noqa: BLE001
        logger.debug("DDG HTML fallback failed for '%s': %s", query, e)

    return hits, used


def discover_for_queries(
    queries: list[str],
    *,
    tavily_api_key: str | None,
    proxy: str | None,
    iteration: int,
    max_hits: int,
    ignored_domains: Sequence[str] = (),
    existing: list[SearchHit] | None = None,
) -> tuple[list[SearchHit], list[str]]:
    """Run discovery for each query; return merged hits and engines used."""
    hits = list(existing or [])
    engines: list[str] = []
    per_query = max(3, max_hits // max(len(queries), 1) + 2)

    for q in queries:
        if len(hits) >= max_hits:
            break
        needed = max_hits - len(hits)
        t_hits, t_used = search_tavily(
            q, api_key=tavily_api_key, max_results=min(per_query, needed), iteration=iteration
        )
        if t_used and "tavily" not in engines:
            engines.append("tavily")
        hits = merge_hits(hits, t_hits, max_hits=max_hits, ignored_domains=ignored_domains)
        if len(hits) >= max_hits:
            break
        d_hits, d_used = search_ddg(
            q,
            max_results=min(per_query, max_hits - len(hits)),
            proxy=proxy,
            iteration=iteration,
            ignored_domains=ignored_domains,
        )
        if d_used and "ddg" not in engines:
            engines.append("ddg")
        hits = merge_hits(hits, d_hits, max_hits=max_hits, ignored_domains=ignored_domains)

    return hits, engines

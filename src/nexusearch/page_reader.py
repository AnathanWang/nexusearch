"""Deep page reading: Firecrawl primary, DNS-pinned https fallback (profile-driven)."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from nexusearch.discovery import extract_domain
from nexusearch.http_util import request_with_retries
from nexusearch.models import PageEvidence, PageSnippet, SearchHit
from nexusearch.profile import SearchProfile
from nexusearch.url_safety import (
    MAX_RESPONSE_BYTES,
    check_url_host,
    is_safe_url,
    pinned_https_get,
    registrable_overlap,
    resolve_public_ip,
)

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_DEFAULT_UA = {
    "User-Agent": "Mozilla/5.0 (compatible; Nexusearch/0.1; +https://example.local)",
    "Accept-Language": "en-US,en;q=0.9",
}


def _deadline_exceeded(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:8000]


def _proxy_fetch_text(
    client: httpx.Client,
    url: str,
    *,
    expected_domain: str,
    attempts: int = 2,
    timeout: float = 8.0,
    deadline: float | None = None,
) -> str:
    """Fetch via trusted proxy (proxy egress; URL still allowlisted)."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if _deadline_exceeded(deadline):
            return ""
        if not is_safe_url(current, expected_domain=expected_domain):
            return ""
        try:
            resp = request_with_retries(
                client,
                "GET",
                current,
                attempts=attempts,
                headers=_DEFAULT_UA,
                timeout=timeout,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("proxy deep-read failed %s: %s", current, e)
            return ""

        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                return ""
            current = urljoin(current, loc)
            continue
        if resp.status_code != 200:
            return ""
        final_host = urlsplit(str(resp.url)).hostname or ""
        if not registrable_overlap(final_host, expected_domain):
            return ""
        # Cap bytes before HTML parse (httpx may already have loaded body).
        raw = resp.content[:MAX_RESPONSE_BYTES]
        try:
            text = raw.decode(resp.encoding or "utf-8", errors="replace")
        except (LookupError, TypeError):
            text = raw.decode("utf-8", errors="replace")
        return _html_to_text(text)
    return ""


def _pinned_fetch_text(
    url: str,
    *,
    expected_domain: str,
    attempts: int = 2,
    timeout: float = 8.0,
    deadline: float | None = None,
) -> str:
    last_text = ""
    for i in range(max(1, attempts)):
        if _deadline_exceeded(deadline):
            return last_text
        status, _final, body = pinned_https_get(
            url,
            expected_domain=expected_domain,
            timeout=timeout,
            max_redirects=_MAX_REDIRECTS,
        )
        if status == 200 and body:
            return _html_to_text(body)
        if status in (429, 500, 502, 503, 504) and i < attempts - 1:
            time.sleep(0.35 * (2**i))
            continue
        last_text = ""
        break
    return last_text


def _firecrawl_fetch(
    url: str,
    api_key: str,
    *,
    expected_domain: str,
    attempts: int = 2,
    deadline: float | None = None,
) -> str:
    """
    Call Firecrawl scrape API via a dedicated client (no page-fetch proxy).

    Local host/DNS checks are advisory only — Firecrawl re-resolves the URL
    out-of-process (separate trust boundary / TOCTOU).
    """
    if _deadline_exceeded(deadline):
        return ""
    host = check_url_host(url, expected_domain=expected_domain)
    if host is None or resolve_public_ip(host) is None:
        logger.debug("Blocked unsafe URL before Firecrawl: %s", url)
        return ""

    endpoint = "https://api.firecrawl.dev/v1/scrape"
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False) as api_client:
            resp = request_with_retries(
                api_client,
                "POST",
                endpoint,
                attempts=attempts,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"url": url, "formats": ["markdown"]},
            )
        if resp.status_code != 200:
            logger.debug("Firecrawl status %s for %s", resp.status_code, url)
            return ""
        data: dict[str, Any] = resp.json()
        inner = data.get("data") or data
        md = inner.get("markdown") or inner.get("content") or ""
        return str(md)[:12000]
    except Exception as e:  # noqa: BLE001
        logger.debug("Firecrawl fetch failed for %s: %s", url, e)
        return ""


def read_domain_evidence(
    hit: SearchHit,
    *,
    profile: SearchProfile,
    firecrawl_api_key: str | None = None,
    proxy: str | None = None,
    max_pages: int = 4,
    max_fetch_attempts: int = 2,
    http_client: httpx.Client | None = None,
    deadline: float | None = None,
    allow_firecrawl: bool = True,
) -> PageEvidence:
    domain = hit.domain or extract_domain(hit.url if hit.url else "")
    if not domain:
        return PageEvidence(domain="")

    root = f"https://{domain}"
    pages: list[PageSnippet] = []
    pages_with_hints = 0
    contributing_excerpt_len = 0
    extracted: dict[str, Any] = {}
    sources: dict[str, str] = {}

    deep_paths = tuple(profile.deep_paths) or ("/",)
    own_client = http_client is None
    client = http_client or httpx.Client(
        proxy=proxy,
        timeout=8.0,
        follow_redirects=False,
    )

    try:
        paths = deep_paths[: max(1, max_pages)]
        for path in paths:
            if _deadline_exceeded(deadline):
                logger.debug("Deep-read deadline hit mid-domain domain=%s", domain)
                break

            if path == "/" and hit.url.startswith("http"):
                raw = hit.url
                url = "https://" + raw[len("http://") :] if raw.startswith("http://") else raw
            elif path == "/":
                url = root + "/"
            else:
                url = urljoin(root + "/", path.lstrip("/"))

            if check_url_host(url, expected_domain=domain) is None:
                continue

            text = ""
            if allow_firecrawl and firecrawl_api_key:
                text = _firecrawl_fetch(
                    url,
                    firecrawl_api_key,
                    expected_domain=domain,
                    attempts=max_fetch_attempts,
                    deadline=deadline,
                )
            if not text:
                try:
                    if proxy:
                        text = _proxy_fetch_text(
                            client,
                            url,
                            expected_domain=domain,
                            attempts=max_fetch_attempts,
                            deadline=deadline,
                        )
                    else:
                        text = _pinned_fetch_text(
                            url,
                            expected_domain=domain,
                            attempts=max_fetch_attempts,
                            deadline=deadline,
                        )
                except Exception as e:  # noqa: BLE001
                    logger.debug("deep-read failed %s: %s", url, e)
                    text = ""

            if not text:
                continue

            excerpt = text[:4000]
            pages.append(PageSnippet(url=url, text_excerpt=excerpt))
            hints = profile.extract_hints(text, url=url)
            before_keys = set(extracted.keys())
            for key, val in hints.items():
                if key not in extracted and val is not None:
                    extracted[key] = val
                    sources[key] = url
            if set(extracted.keys()) != before_keys:
                pages_with_hints += 1
                contributing_excerpt_len = max(contributing_excerpt_len, len(excerpt))

            if len(pages) >= max_pages:
                break
    finally:
        if own_client:
            client.close()

    has_any = bool(extracted) and bool(pages)
    confidence = None
    if pages:
        try:
            confidence = profile.score_confidence(
                pages,
                pages_with_hints=pages_with_hints,
                has_any_hint=has_any,
                contributing_excerpt_len=contributing_excerpt_len,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("score_confidence failed domain=%s: %s", domain, e)

    return PageEvidence(
        domain=domain,
        pages=pages,
        extracted=extracted if pages else {},
        sources=sources if pages else {},
        hint_confidence=confidence if pages else None,
    )


def deep_read_hits(
    hits: list[SearchHit],
    *,
    profile: SearchProfile,
    max_deep_read: int,
    firecrawl_api_key: str | None = None,
    proxy: str | None = None,
    max_fetch_attempts: int = 2,
    max_deep_read_seconds: float = 25.0,
    allow_firecrawl: bool = True,
) -> list[SearchHit]:
    enriched: list[SearchHit] = []
    deadline = time.monotonic() + max(0.0, max_deep_read_seconds)
    client = httpx.Client(proxy=proxy, timeout=8.0, follow_redirects=False)
    try:
        for i, hit in enumerate(hits):
            if i < max_deep_read:
                if _deadline_exceeded(deadline):
                    logger.debug("Deep-read wall clock budget exhausted after %s domains", i)
                    enriched.extend(hits[i:])
                    break
                evidence = read_domain_evidence(
                    hit,
                    profile=profile,
                    firecrawl_api_key=firecrawl_api_key,
                    proxy=proxy,
                    max_fetch_attempts=max_fetch_attempts,
                    http_client=client,
                    deadline=deadline,
                    allow_firecrawl=allow_firecrawl,
                )
                if evidence.pages:
                    hit = hit.model_copy(update={"page_evidence": evidence})
            enriched.append(hit)
    finally:
        client.close()
    return enriched

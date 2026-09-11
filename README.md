# nexusearch

Universal **multi-iteration live web discovery** + optional **deep page reading**.

Domain behavior is **not** built in. Pass a `SearchProfile` (queries, ignored domains, deep-read paths, hint extractors).

No invented results. No offline curated fallback.

## Install

```bash
# From a sibling checkout (S3-Nexus style):
uv add --editable ../nexusearch

# Optional Tavily:
uv add --editable "../nexusearch[tavily]"
```

Or from git once published:

```toml
nexusearch = { git = "https://github.com/<org>/nexusearch", rev = "main" }
```

## Quick start

```python
from typing import Any
from nexusearch import NexusSearchClient, NexusSearchOptions, SearchProfile

class MyProfile:
    name = "my-domain"
    ignored_domains = ("amazon.", "wikipedia.")
    deep_paths = ("/", "/about")

    def plan_iter1(self, query: str, *, llm, max_queries: int) -> list[str]:
        return [query][:max_queries]

    def plan_iter2(self, hits, *, already_used, llm, max_queries: int) -> list[str]:
        return []

    def extract_hints(self, text: str, *, url: str) -> dict[str, Any]:
        return {}

    def score_confidence(self, pages, *, pages_with_hints, has_any_hint, contributing_excerpt_len=0):
        return None

client = NexusSearchClient(
    profile=MyProfile(),
    tavily_api_key="tvly-...",   # optional
    firecrawl_api_key="fc-...",  # optional
)

bundle = client.search("camping furniture", NexusSearchOptions(max_hits=20, max_deep_read=8))
for hit in bundle.hits:
    print(hit.domain, hit.iteration, hit.page_evidence)
print(bundle.meta)
```

## Pipeline

1. **Iter 1** — `profile.plan_iter1`
2. **Discover** — Tavily (if key) + DuckDuckGo; merge unique domains; apply `profile.ignored_domains`
3. **Iter 2** — `profile.plan_iter2` (optional)
4. **Deep-read** — Firecrawl or DNS-pinned https on `profile.deep_paths`; hints via `profile.extract_hints`

## Safety

- Direct deep-read is **https-only**, DNS-pinned, and allows **only global IPs** (`ip.is_global`) — CGNAT/`100.64.0.0/10` and other non-global ranges are rejected.
- Response bodies are capped (`MAX_RESPONSE_BYTES`, default 2 MiB).
- Deep-read respects a hard monotonic deadline across domains **and** paths.
- Proxy mode is allowlist + DNS blocklist (proxy is a trust boundary).
- **Firecrawl** is an out-of-process cloud fetch: local DNS pre-check is **advisory** (TOCTOU). API calls use a dedicated httpx client (not the page-fetch proxy). Disable with `NexusSearchOptions(allow_firecrawl=False)`.

## Meta telemetry

- `meta.engines` — adapters that returned a successful response (attempted)
- `meta.engines_with_hits` — adapters that contributed at least one merged domain

## Env

- `TAVILY_API_KEY`
- `FIRECRAWL_API_KEY`
- `NEXUSEARCH_PROXY_URL` (also accepts legacy `DEEP_SEARCH_PROXY_URL` / `S3_SCOUT_PROXY_URL`)

## Develop

```bash
uv sync --extra dev
uv run pytest
```

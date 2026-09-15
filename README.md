# nexusearch

Universal **multi-iteration live web discovery** + optional **deep page reading**, driven by a declarative search scenario.

The library is a smart but **blind** search agent: domain behavior is **not** built in. You hand it a scenario ("Ориентировка") — where to search, how to expand queries, what garbage to reject, which facts to extract.

**No invented results. No offline curated fallback.**

- Python ≥ 3.11 · MIT license · [Changelog](CHANGELOG.md) · [Usage guide (подробно)](docs/USAGE.md) · [Cookbook (рецепты)](docs/COOKBOOK.md)

## Install

```toml
# pyproject.toml of the consumer app
nexusearch = { git = "https://github.com/AnathanWang/nexusearch", rev = "v0.5.2" }
```

Optional extras:

```bash
uv add "nexusearch[tavily]"   # Tavily adapter
uv add "nexusearch[redis]"    # RedisSearchCache backend
uv add "nexusearch[dev]"      # pytest + ruff (development)
```

API keys are **always owned by the consumer**: pass them explicitly to the client constructor (or use `NexusSearchSettings.from_env()`, which reads only provider-standard names). The library never reads app-level env vars.

## Quick start — declarative scenario (no classes)

```python
from nexusearch import NexusSearchClient, NexusSearchOptions, SimpleSearchProfile
from nexusearch.hints import EMAIL_PATTERN, PHONE_PATTERN

profile = SimpleSearchProfile(
    name="horeca_b2b",
    description="HoReCa B2B suppliers",
    query_templates=[
        "{query} HoReCa опт поставка",
        "{query} оптовый поставщик прайс-лист",
    ],
    ignored_domains=["eda.ru", "delivery-club."],   # blacklist
    stop_words=["рецепт", "как приготовить"],        # red flags → reject domain
    required_words=["прайс", "отгрузка"],            # green flags → keep domain
    hint_patterns={"email": EMAIL_PATTERN, "phone": PHONE_PATTERN},
)

client = NexusSearchClient(
    profile=profile,
    tavily_api_key="tvly-...",      # optional
    brave_api_key="brave-...",      # optional
    firecrawl_api_key="fc-...",     # optional, for deep-read
)

bundle = client.search("молочные продукты", NexusSearchOptions(max_hits=20, max_deep_read=8))
for hit in bundle.hits:
    print(hit.domain, hit.page_evidence.extracted if hit.page_evidence else {})
print(bundle.meta)  # engines, queries, rejected_count, profile, ...
```

## The levers of a scenario

| Lever | How |
|---|---|
| Query expansion | `query_templates`, `iter2_template` (or full `plan_iter1/2` in a custom profile) |
| Geography | `ignored_domains` (blacklist), `allowed_domains` + `url_path_patterns` (whitelist, hard filter) |
| Result filtering | `stop_words` → `reject_page`, `required_words` → `accept_domain` (content policy on fetched pages) |
| Fact extraction | `hint_patterns` (regex), `int_hints` |
| Custom channels | `DomainChannelAdapter` — declarative channel (exhibitions, directories) over any SERP backend |
| Multi-scenario | `RoutedSearchClient` + `RuleRouter`/`HybridRouter` — pick a profile per query |

Need full control (LLM query planning, custom scoring)? Implement the `SearchProfile` protocol directly — `SimpleSearchProfile` is just a convenience over it.

## Pipeline

1. **Route** *(optional)* — pick scenario by query (`RoutedSearchClient`)
2. **Iter 1** — `profile.plan_iter1` → query expansion
3. **Discover** — adapters in parallel (`Tavily` / `Brave` / `SerpAPI` / `DDG` / custom), merged by unique domain, `ignored_domains` applied
4. **Iter 2** — `profile.plan_iter2` (optional, brand-based re-query)
5. **Geo filter** — profile-level `allowed_domains` / `url_path_patterns`
6. **Deep-read** — Firecrawl or DNS-pinned HTTPS on `profile.deep_paths`; content policy (`reject_page` / `accept_domain`) drops garbage domains; hints via `extract_hints`

## Adapters & channels

| Adapter | Channel | Needs |
|---|---|---|
| `TavilyAdapter` | `serp` | `tavily` extra + key |
| `BraveAdapter` | `serp` | key |
| `SerpApiAdapter` | `serp` | key |
| `DuckDuckGoAdapter` | `serp` | — (built-in, library + HTML fallback) |
| `LlmGroundedAdapter` | `llm` | `LlmJsonClient` (citations→hits only, URL gate) |
| `DomainChannelAdapter` | any | declarative: templates + domain/path filters over a SERP backend |

Profiles restrict channels via `channels=("serp", ...)`; the client filters adapters accordingly. All adapters accept optional `rate_limiter=` / `circuit_breaker=` guards — an exhausted limiter or open breaker returns `([], False)` ("not attempted"), never a fake empty result.

## Ops

- `RateLimiter(rate, per_seconds)` — thread-safe token bucket
- `CircuitBreaker(failure_threshold, cooldown_seconds)` — open after N consecutive failures, half-open after cooldown
- `CostBudget(max_credits)` — credits budget for paid APIs
- `SearchCache` / `RedisSearchCache` — TTL cache, same key format
- `Budget(max_seconds)` — wall-clock guard
- Adapters reuse one `httpx.Client` per instance (thread-safe); call `adapter.close()` on teardown
- `SearchHooks` — `on_search_start/queries_planned/hit_discovered/search_end/search_error` (sync & async; hook errors are logged, never raised)
- `AsyncNexusSearchClient` — async facade over the sync pipeline

## Safety

- Direct deep-read is **HTTPS-only**, DNS-pinned, and allows **only global IPs** (`ip.is_global`) — CGNAT/`100.64.0.0/10` and other non-global ranges are rejected.
- Response bodies are capped (`MAX_RESPONSE_BYTES`, default 2 MiB).
- Deep-read respects a hard monotonic deadline across domains **and** paths.
- Proxy mode is allowlist + DNS blocklist (proxy is a trust boundary).
- **Firecrawl** is an out-of-process cloud fetch: local DNS pre-check is **advisory** (TOCTOU). API calls use a dedicated httpx client (not the page-fetch proxy). Disable with `NexusSearchOptions(allow_firecrawl=False)`.
- SerpAPI adapter pins the httpx logger to WARNING (the API key travels in the query string).

## Meta telemetry

- `meta.engines` / `meta.engines_with_hits` — adapters attempted / contributed
- `meta.profile` — scenario name (also set by routing)
- `meta.rejected_count` — domains dropped by content policy
- `meta.queries_used`, `meta.iterations_run`, `meta.deep_read_count`

## Env

`NexusSearchSettings.from_env()` reads only these (everything else is passed explicitly):

- `TAVILY_API_KEY`, `BRAVE_API_KEY`, `SERPAPI_API_KEY`, `FIRECRAWL_API_KEY`
- `NEXUSEARCH_PROXY_URL`

## Develop

```bash
uv sync --extra dev --extra tavily
uv run pytest                 # unit tests (live tests skip by default)
uv run ruff check src tests
NEXUSEARCH_LIVE_TESTS=1 uv run pytest tests/test_live_smoke.py   # opt-in, real APIs
```

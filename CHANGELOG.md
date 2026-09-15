# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(0.x: minor bumps may contain breaking changes until 1.0).

## [0.5.2] — 2026-09-15

### Added

- `py.typed` marker — consumers' type checkers now use the library's annotations.
- CI: GitHub Actions (ruff + pytest matrix py3.11–3.13 + coverage gate 75% +
  wheel build), nightly live-smoke workflow (opt-in, `TAVILY_API_KEY` secret),
  trusted-publishing workflow for PyPI releases on `v*` tags.
- `SECURITY.md` — vulnerability reporting policy.
- Tests: `test_http_util.py`, `test_redis_cache.py` (redis_cache 100%).

### Changed

- Dependency: deprecated `duckduckgo-search` replaced by its successor `ddgs`.
- Adapters now reuse one lazily-created `httpx.Client` / `TavilyClient` per
  instance (thread-safe) instead of a fresh client per query — fewer TLS
  handshakes under load. New `close()` on adapters for explicit teardown.

## [0.5.1] — 2026-09-14

### Changed

- Internal refactor, no public API changes: shared hit-builder + guard
  helpers across discovery adapters, DDG adapter split into library/scrape
  strategies with a single exit point (breaker now records on every path),
  `_merge_unique` / `_discover` in the client, typed `hooks` param,
  `NexusSearchSettings.as_client_kwargs()` removes the triple-duplicated
  settings→kwargs mapping, `RuleRouter._match_rule` shared with HybridRouter,
  fetch strategy chain extracted in page_reader, shared `backoff_delay`.

## [0.5.0] — 2026-09-14

### Added

- **Ops hardening**: `RateLimiter` (thread-safe token bucket),
  `CircuitBreaker` (open after N consecutive failures, half-open after
  cooldown), `CostBudget` (credits budget for paid APIs).
- **Adapter guards**: all built-in discovery adapters (Tavily, Brave, SerpAPI,
  DuckDuckGo) accept optional `rate_limiter` / `circuit_breaker`. An exhausted
  limiter or open breaker returns `([], False)` — "not attempted", never a
  fake empty result; breaker records failure only when the call actually
  fails, success only on a real response.
- **`RedisSearchCache`**: multi-instance cache backend, same key format as
  `SearchCache` (extra: `pip install "nexusearch[redis]"`).
- `tests/test_live_smoke.py`: opt-in live checks against real APIs
  (`NEXUSEARCH_LIVE_TESTS=1`).
- COOKBOOK: sections on API quotas and Prometheus metrics via `SearchHooks`.

## [0.4.1] — 2026-09-14

### Changed

- `NexusSearchSettings.from_env()` no longer reads consumer-specific env
  fallbacks (`DEEP_SEARCH_PROXY_URL`, `S3_SCOUT_PROXY_URL`) — only
  `NEXUSEARCH_*` / provider variables. API keys are passed explicitly by the
  consumer application, never read by the library from app-level config.

## [0.4.0] — 2026-09-11

### Added

- **Declarative search scenarios**: `SimpleSearchProfile` — all scenario
  "levers" via constructor (query templates, ignored/allowed domains, URL path
  patterns, stop-words, required-words, hint patterns, int hints).
- **`DomainChannelAdapter`**: declarative custom channel — query templates +
  domain whitelist + path regexes over any SERP backend, no subclassing.
- **Content policy**: optional profile hooks `reject_page(text)` and
  `accept_domain(domain_text)` applied during deep-read; rejected domains are
  dropped from the bundle.
- **Page geo-filter**: profile-level `allowed_domains` / `url_path_patterns`
  hard-filter in `client.search()`.
- **Scenario routing**: `RouteRule` / `RuleRouter` / `HybridRouter` (rules +
  LLM fallback) / `RoutedSearchClient` — pick a search profile per query with
  per-scenario channel isolation.
- **`nexusearch.hints`**: ready-made `EMAIL_PATTERN` / `PHONE_PATTERN`.
- Telemetry: `PageEvidence.rejected`, `SearchMeta.profile`,
  `SearchMeta.rejected_count`.

### Changed

- **Breaking**: `deep_read_hits()` now returns `tuple[list[SearchHit], int]`
  (hits, rejected_count) instead of `list[SearchHit]`.

## [0.3.0] — 2026-09-11

### Removed

- **Breaking**: `ExpoAdapter` moved out of the library core. Domain channels
  live in consumer apps (later superseded by `DomainChannelAdapter` in 0.4.0).

## [0.2.1] — 2026-09-11

### Fixed

- Remediation of the full code review (28 findings): config validation,
  SSRF/redirect edge cases, error taxonomy, timeout handling, docs.

## [0.2.0] — 2026-09-11

### Added

- `DiscoveryAdapter` protocol + hit provenance (`source_adapter`, `channel`).
- `AsyncNexusSearchClient` + `SearchHooks` middleware.
- `BraveSearchAdapter`, `SerpApiAdapter`; parallel adapter fan-out with
  per-adapter deadline and dedupe.
- `LlmGroundedAdapter` (citations→hits only, URL gate, no-invent).
- Ops helpers: `SearchCache` (TTL), `Budget` (wall-clock guard), `RateLimiter`
  (token bucket); smoke checks; `docs/COOKBOOK.md`.

## [0.1.0] — 2026-09-10

### Added

- Initial extraction from the S3 app into a standalone generic library:
  profile-driven search over Tavily / Firecrawl / DDG, deep-read page pipeline,
  domain-agnostic `PageEvidence.extracted` dict.
- SSRF hardening: CGNAT range blocking, 2 MB body cap, deep-read deadlines,
  Firecrawl client isolation.

[0.5.2]: https://github.com/AnathanWang/nexusearch/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/AnathanWang/nexusearch/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/AnathanWang/nexusearch/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/AnathanWang/nexusearch/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/AnathanWang/nexusearch/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/AnathanWang/nexusearch/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/AnathanWang/nexusearch/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AnathanWang/nexusearch/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AnathanWang/nexusearch/releases/tag/v0.1.0

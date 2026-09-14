# Cookbook

Рецепты по использованию nexusearch.

## Ключи и конфигурация

Ключи всегда принадлежат проекту-потребителю и передаются **явно** — библиотека
не читает env сама и не знает чужих имён переменных:

```python
from nexusearch import NexusSearchClient

client = NexusSearchClient(
    profile=my_profile,
    tavily_api_key=settings.tavily_api_key,      # из settings ВАШЕГО проекта
    brave_api_key=settings.brave_api_key,
    serpapi_api_key=settings.serpapi_api_key,
    firecrawl_api_key=settings.firecrawl_api_key,
    proxy_url=settings.my_proxy_url,
)
```

Тот же набор ключей принимают `AsyncNexusSearchClient` и `RoutedSearchClient`
(через `**client_kwargs`).

Опциональный шорткат — `NexusSearchClient.from_env(profile=...)`: читает только
provider-стандартные имена (`TAVILY_API_KEY`, `BRAVE_API_KEY`, `SERPAPI_API_KEY`,
`FIRECRAWL_API_KEY`) и `NEXUSEARCH_PROXY_URL`. Если в вашем проекте переменные
называются иначе (напр. `S3_SCOUT_PROXY_URL`) — читайте их в своих settings и
передавайте явно.

Адаптеры без ключей просто не активируются: без единого ключа работает
бесплатный `DuckDuckGoAdapter`.

## LLM grounded search without invent

`LlmGroundedAdapter` превращает grounded-ответы LLM (Perplexity sonar, OpenAI
responses + web_search, Gemini grounding — любой клиент с `generate_json`) в
`SearchHit` **только при наличии валидного https URL**.

Правила ядра (не отключаются):

1. Кандидат без URL или с не-https / заблокированным хостом (SSRF-gate
   `check_url_host`) → отбрасывается в `adapter.discarded_entities`,
   **никогда** в `hits`.
2. Домены из `profile.ignored_domains` → отбрасываются.
3. Имя без URL — это не лид. Точка.

```python
from nexusearch import LlmGroundedAdapter, NexusSearchClient, SearchProfile

class MyLlm:  # обёртка над Perplexity / OpenAI / Gemini
    def generate_json(self, prompt: str):
        # должен вернуть {"candidates": [{"name","url","why","confidence"}]}
        ...

profile: SearchProfile = ...  # ваш профиль
client = NexusSearchClient(
    profile=profile,
    adapters=[
        # SERP остаётся основным каналом; LLM — дополнительный recall
        *default_adapters(tavily_api_key="..."),
        LlmGroundedAdapter(MyLlm(), name="llm_perplexity"),
    ],
)
bundle = client.search("us manufacturers of camping furniture")
for hit in bundle.hits:
    print(hit.channel, hit.source_adapter, hit.url, hit.grounding_score)
```

Политика ранжирования для потребителей: hits с `channel="llm_grounded"` не
должны ранжироваться выше SERP-хитов того же домена без deep-read
подтверждения.

## Каналы в профиле

`SearchProfile.channels` ограничивает, какие адаптеры реально работают:

```python
class SupplierProfile:
    channels = ("serp", "llm_grounded")   # без expo

class ExpoProfile:
    channels = ("expo", "serp")
```

## Ориентировка: сценарий поиска без классов (SimpleSearchProfile)

Библиотека — умный, но слепой агент. `SimpleSearchProfile` — это «ориентировка»,
которую разработчик передаёт агенту одним конфигом:

```python
from nexusearch import NexusSearchClient, SimpleSearchProfile
from nexusearch.hints import EMAIL_PATTERN, PHONE_PATTERN

horeca = SimpleSearchProfile(
    name="horeca_b2b",
    description="HoReCa B2B suppliers (оптовики продуктов для ресторанов)",

    # Словарь: одно слово юзера → несколько прокачанных запросов
    query_templates=[
        "{query} HoReCa опт поставка",
        "{query} оптовый поставщик прайс-лист",
        "{query} дистрибьютор продуктов для ресторанов",
    ],
    iter2_template='"{brand}" прайс опт',   # докрутка по найденным брендам

    # География
    ignored_domains=["eda.ru", "delivery-club.", "avito.", "ozon.", "wildberries."],
    # allowed_domains=["b2b-center.ru", "supl.biz"],  # whitelist-режим (опц.)
    # url_path_patterns=[r"/(wholesale|opt|b2b)"],     # только такие URL (опц.)

    # Детектор мусора (применяется к скачанным страницам в deep-read)
    stop_words=["рецепт", "как приготовить", "калорийность", "положить в корзину"],
    required_words=["прайс", "отгрузка"],  # хотя бы одно обязано быть на домене

    # Извлечение фактов
    hint_patterns={"email": EMAIL_PATTERN, "phone": PHONE_PATTERN},

    deep_paths=("/", "/about", "/price", "/opt"),
)

client = NexusSearchClient(profile=horeca, tavily_api_key="...")
bundle = client.search("мраморная говядина")
for hit in bundle.hits:
    print(hit.url, hit.page_evidence.extracted if hit.page_evidence else {})
print(bundle.meta.rejected_count, "сайтов забраковано")  # детектор мусора
```

Как работает детектор мусора:

- `stop_words` (красные флаги): любое слово на скачанной странице → домен
  бракуется немедленно, остальные страницы домена не скачиваются.
- `required_words` (зелёные флаги): если заданы, хотя бы одно слово должно
  встретиться в объединённом полном тексте всех страниц домена.
- Забракованные хиты **удаляются** из результатов; счётчик — `meta.rejected_count`.
- Валидация best-effort: работает только на реально скачанных страницах
  (`deep_read=True`). Сайт, который не удалось скачать, остаётся без evidence,
  но НЕ бракуется.

Ограничение: `SimpleSearchProfile` планирует запросы детерминированно (только
шаблоны). Нужен LLM-планning — реализуйте полный протокол `SearchProfile`.

## Свой канал декларативно (DomainChannelAdapter)

Канал discovery (выставки, каталоги, отраслевые площадки) без написания класса:

```python
from nexusearch import DomainChannelAdapter, NexusSearchClient, default_adapters

expo = DomainChannelAdapter(
    name="expo",
    query_templates=["{query} trade show exhibitors list"],
    allowed_domains=["10times.com", "expocentr.ru"],           # exact/subdomain
    path_patterns=[r"(?:^|[/._-])(expos?|exhibitors?)(?:[/._-]|$)"],
    # backend=DuckDuckGoAdapter() по умолчанию; можно передать любой SERP-адаптер
)

profile = SimpleSearchProfile(
    name="expo",
    channels=("expo", "serp"),   # профиль решает, какие каналы активны
    query_templates=["{query} trade show exhibitors list", "{query} expo exhibition"],
)
client = NexusSearchClient(profile=profile, adapters=[*default_adapters(), expo])
```

Хит попадает в канал, если его домен в `allowed_domains` ИЛИ путь URL матчит
`path_patterns`. Provenance проставляется автоматически (`source_adapter`,
`channel`, `discovery_query`).

## Мульти-сценарий: авто-выбор по запросу (роутинг)

Несколько сценариев + гибридный роутер (правила → LLM → default):

```python
from nexusearch import (
    DomainChannelAdapter, HybridRouter, RoutedSearchClient, RouteRule,
    SimpleSearchProfile, default_adapters,
)

router = HybridRouter(
    rules=[RouteRule("expo", r"выставк|expo|trade.?show")],
    default="supplier",
)
client = RoutedSearchClient(
    profiles={"supplier": supplier_profile, "expo": expo_profile},
    router=router,
    llm=my_llm,                      # опционально; без него — только правила
    adapters=[*default_adapters(), expo_adapter],
    tavily_api_key="...",
)
bundle = client.search("выставка кемпингового снаряжения")
bundle.meta.profile  # "expo" — какой сценарий сработал
```

- Правила — case-insensitive regex, первое совпадение выигрывает.
- Если ни одно правило не сработало и передан `llm` — LLM классифицирует
  запрос по именам и `description` сценариев; мусорный/неизвестный ответ →
  default.
- Каждый сценарий получает свой `NexusSearchClient` со своей фильтрацией
  каналов: expo-адаптер не вызывается для supplier-запросов и наоборот.

## Свой канал discovery (custom adapter)

Доменные каналы (expo, directories, sitemap…) живут **в потребителе**, не в
ядре. Достаточно реализовать протокол:

```python
class ExpoAdapter:  # живёт в вашем репо, не в nexusearch
    name = "expo"
    channel = "expo"

    def __init__(self, backend):  # backend — любой SERP-адаптер
        self.backend = backend

    def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
        raw, used = self.backend.discover(
            f"{query} trade show exhibitors list",
            max_results=max_results * 3, iteration=iteration,
            ignored_domains=ignored_domains,
        )
        hits = [h.model_copy(update={"channel": "expo", "source_adapter": "expo"})
                for h in raw if self._is_expo(h.url, h.domain)]
        return hits[:max_results], used

client = NexusSearchClient(profile=ExpoProfile(), adapters=[*default_adapters(), ExpoAdapter(DuckDuckGoAdapter())])
```

Контракт: `discover` возвращает `(hits, attempted_ok)`; адаптер обязан быть
reentrant (вызывается из пула потоков при `parallel_adapters=True`); падение
адаптера изолировано ядром (логируется, деградирует в `([], False)`).

## Кэш и бюджеты

```python
from nexusearch import Budget, SearchCache

cache = SearchCache(ttl_seconds=900)
key = SearchCache.key(profile.name, query)
bundle = cache.get(key) or client.search(query)
cache.set(key, bundle)

budget = Budget(max_seconds=45)
# проверяйте budget.expired между тяжёлыми стадиями в своих адаптерах
```

### Квоты платных API: RateLimiter, CircuitBreaker, CostBudget

```python
from nexusearch import BraveAdapter, CircuitBreaker, CostBudget, RateLimiter, TavilyAdapter

# Не больше 30 вызовов Tavily в минуту; при исчерпании адаптер
# честно возвращает ([], False) — "не пытался", а не "пустой результат".
tavily_limiter = RateLimiter(30, per_seconds=60.0)

# После 3 подряд сетевых ошибок адаптер молчит 5 минут (half-open после).
brave_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)

# Жёсткий денежный лимит: charge() вернёт False, когда кредиты кончатся.
credits = CostBudget(max_credits=500)

adapters = [
    TavilyAdapter(tavily_key, rate_limiter=tavily_limiter),
    BraveAdapter(brave_key, circuit_breaker=brave_breaker),
]
# CostBudget подключается в своих адаптерах:
#     if not credits.charge(): return [], False
```

Один и тот же limiter/breaker можно шарить между адаптерами — оба потокобезопасны
(`parallel_adapters=True` гоняет адаптеры в пуле потоков).

## Метрики (Prometheus)

Хуки уже встроены в клиент — метрики подключаются без правок библиотеки.
Ошибки хуков глушатся `HookPipeline` и логируются: метрики не могут уронить поиск.

```python
from prometheus_client import Counter, Histogram


class PrometheusHooks:
    def __init__(self) -> None:
        self.searches = Counter("nexusearch_searches_total", "Searches", ["profile"])
        self.errors = Counter("nexusearch_errors_total", "Search errors", ["profile"])
        self.latency = Histogram("nexusearch_search_seconds", "Search latency", ["profile"])
        self.rejected = Counter("nexusearch_rejected_total", "Rejected domains", ["profile"])

    def on_search_end(self, bundle, *, elapsed_ms: float) -> None:
        profile = bundle.meta.profile or "unknown"
        self.searches.labels(profile).inc()
        self.latency.labels(profile).observe(elapsed_ms / 1000)
        if bundle.meta.rejected_count:
            self.rejected.labels(profile).inc(bundle.meta.rejected_count)

    def on_search_error(self, error, *, elapsed_ms: float) -> None:
        self.errors.labels("unknown").inc()


# client = NexusSearchClient(profile=..., adapters=..., hooks=[PrometheusHooks()])
```

Доступные хуки: `on_search_start(query, options)`,
`on_queries_planned(queries, iteration=...)`, `on_hit_discovered(hit)`,
`on_search_end(bundle, elapsed_ms=...)`, `on_search_error(error, elapsed_ms=...)`.
Реализовывать можно любое подмножество — отсутствующие методы просто пропускаются.

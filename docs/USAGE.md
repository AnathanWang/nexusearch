# nexusearch — подробное руководство по использованию

> Версия документа: `v0.5.2`. Быстрый обзор — в [README](../README.md),
> готовые рецепты — в [COOKBOOK](COOKBOOK.md), история — в [CHANGELOG](../CHANGELOG.md).

## Содержание

1. [Ментальная модель](#1-ментальная-модель)
2. [Установка](#2-установка)
3. [Быстрый старт за 5 минут](#3-быстрый-старт-за-5-минут)
4. [Как работает пайплайн](#4-как-работает-пайплайн)
5. [Ключи и конфигурация](#5-ключи-и-конфигурация)
6. [Управление выдачей: NexusSearchOptions](#6-управление-выдачей-nexussearchoptions)
7. [Сценарий поиска: SimpleSearchProfile](#7-сценарий-поиска-simplesearchprofile)
8. [Полный протокол SearchProfile](#8-полный-протокол-searchprofile)
9. [Адаптеры и каналы](#9-адаптеры-и-каналы)
10. [Контент-политика: отбраковка мусора](#10-контент-политика-отбраковка-мусора)
11. [Мульти-сценарий: роутинг](#11-мульти-сценарий-роутинг)
12. [Модели данных](#12-модели-данных)
13. [Ops: лимиты, брейкеры, кэш, хуки, async](#13-ops-лимиты-брейкеры-кэш-хуки-async)
14. [Интеграция в свой проект: чек-лист](#14-интеграция-в-свой-проект-чек-лист)
15. [Диагностика: почему пусто](#15-диагностика-почему-пусто)
16. [Безопасность](#16-безопасность)
17. [FAQ](#17-faq)

---

## 1. Ментальная модель

Библиотека — **«умный, но слепой» поисковый агент**. Она умеет механику
(расширение запросов, параллельный опрос поисковиков, глубокое чтение
страниц, отбраковка мусора, извлечение фактов), но **ничего не знает о вашей
предметной области**.

Чтобы она работала на вас, вы выдаёте ей **«ориентировку»** — декларативный
сценарий поиска (`SimpleSearchProfile`) или полный класс-протокол
(`SearchProfile`):

| Рычаг | Что настраивает |
|---|---|
| География поиска | белый/чёрный списки доменов, паттерны URL-путей |
| Расширение запросов | шаблоны запросов, мультизапросы, итерация 2 |
| Фильтрация результатов | стоп-слова (красные флаги) и обязательные слова (зелёные флаги) |
| Извлечение фактов | regex-подсказки: email, телефон, что угодно своё |

Два железных правила:

- **No invented results** — каждый хит пришёл из реального ответа адаптера;
  LLM-адаптер пропускает только сущности с валидным https-URL.
- **No offline fallback** — если сеть/ключи недоступны, вы получите пустой
  результат с честной телеметрией, а не выдуманные данные.

## 2. Установка

```toml
# pyproject.toml вашего проекта
[project]
dependencies = ["nexusearch"]

[tool.uv.sources]
nexusearch = { git = "https://github.com/AnathanWang/nexusearch.git", rev = "v0.5.2" }
```

Extras:

```bash
uv add "nexusearch[tavily]"   # Tavily-адаптер
uv add "nexusearch[redis]"    # RedisSearchCache
uv add "nexusearch[dev]"      # pytest + ruff + pytest-cov (разработка)
```

Требования: Python ≥ 3.11. Зависимости ядра: `httpx`, `beautifulsoup4`,
`pydantic`, `ddgs` (keyless DuckDuckGo).

## 3. Быстрый старт за 5 минут

Минимальный рабочий пример — без единого ключа API (DDG бесплатный):

```python
from nexusearch import NexusSearchClient, NexusSearchOptions, SimpleSearchProfile

profile = SimpleSearchProfile(
    name="suppliers",
    query_templates=[
        "{query} производитель каталог",
        "{query} оптовый поставщик прайс",
    ],
    ignored_domains=["amazon.", "wildberries."],
)

client = NexusSearchClient(profile=profile)
bundle = client.search("cnc станки", NexusSearchOptions(max_hits=10, deep_read=False))

for hit in bundle.hits:
    print(hit.domain, "—", hit.title)
print(bundle.meta.engines, bundle.meta.engines_with_hits)
```

`deep_read=False` — только выдача поисковиков, без скачивания страниц
(быстро). Включите deep-read, когда добавите `hint_patterns` / контент-политику.

## 4. Как работает пайплайн

```
query
  │
  ├─ 0. ROUTE (опционально)      RoutedSearchClient выбирает сценарий
  │
  ├─ 1. PLAN iter1               profile.plan_iter1: шаблоны/LLM → N запросов
  │
  ├─ 2. DISCOVER                 адаптеры (параллельно или последовательно)
  │                              merge по уникальному домену, ignored_domains
  │
  ├─ 3. PLAN iter2 (опционально) plan_iter2 по найденным брендам, если хитов < max_hits
  │                              → DISCOVER ещё раз
  │
  ├─ 4. GEO FILTER               allowed_domains / url_path_patterns (hard filter)
  │
  └─ 5. DEEP-READ (опционально)  по deep_paths: Firecrawl → proxy → DNS-pinned
                                 ├── reject_page(text) → домен выбрасывается сразу
                                 ├── extract_hints(text) → факты в PageEvidence
                                 ├── accept_domain(texts) → финальная проверка
                                 └── score_confidence(pages) → LOW/MEDIUM/HIGH
```

Важные инварианты:

- **Дедупликация по домену**: один домен = один хит в выдаче.
- **`attempted_ok=False`** у адаптера означает «не пытался / не смог» — такой
  адаптер не попадает в `meta.engines` и не маскирует отказ под «пустой ответ».
- **Deep-read best-effort**: страница не скачалась → хит остаётся без
  `page_evidence`; домен отклонён политикой → хит выбрасывается и считается в
  `meta.rejected_count`.

## 5. Ключи и конфигурация

**Ключи принадлежат потребителю.** Библиотека никогда не читает env вашего
приложения; вы передаёте ключи явно:

```python
client = NexusSearchClient(
    profile=profile,
    tavily_api_key=settings.tavily_key,      # ваш settings-модуль
    brave_api_key=settings.brave_key,
    serpapi_api_key=settings.serpapi_key,
    firecrawl_api_key=settings.firecrawl_key,
    proxy_url=settings.proxy_url,
)
```

Для простых случаев есть конвенция — `NexusSearchSettings.from_env()` читает
**только** обще-провайдерские имена:

| Env | Назначение |
|---|---|
| `TAVILY_API_KEY` | Tavily |
| `BRAVE_API_KEY` | Brave Search |
| `SERPAPI_API_KEY` | SerpAPI |
| `FIRECRAWL_API_KEY` | Firecrawl (deep-read) |
| `NEXUSEARCH_PROXY_URL` | прокси для page-fetch |

```python
client = NexusSearchClient.from_env(profile=profile)
```

Никаких fallback'ов на чужие переменные (`S3_*`, `MYAPP_*`) не будет никогда —
это сознательное ограничение, см. CHANGELOG 0.4.1.

## 6. Управление выдачей: NexusSearchOptions

Полная таблица ручек (все со здравыми дефолтами):

| Поле | Дефолт | Что делает |
|---|---|---|
| `max_hits` | 20 | **итоговое число хитов** (уникальных доменов) |
| `max_iter1_queries` | 5 | сколько запросов из iter1 реально выполняется |
| `enable_iter2` | True | разрешить вторую итерацию |
| `max_iter2_queries` | 3 | сколько запросов из iter2 |
| `deep_read` | True | включить глубокое чтение |
| `max_deep_read` | 10 | сколько доменов deep-читаем |
| `max_domains_before_deep_read` | 30 | верхний кап на deep-read бюджет |
| `max_deep_read_seconds` | 25.0 | жёсткий wall-clock дедлайн на все deep-read |
| `max_fetch_attempts` | 2 | попыток на одну страницу |
| `allow_firecrawl` | True | использовать Firecrawl, если ключ есть |
| `parallel_adapters` | False | адаптеры параллельно (thread pool) |
| `proxy_url` | None | прокси на этот вызов (переопределяет клиентский) |

Примеры:

```python
# Быстрая разведка: только SERP, до 5 доменов
NexusSearchOptions(max_hits=5, deep_read=False)

# Глубокий прогон: 50 доменов, читаем 20, до 2 минут
NexusSearchOptions(max_hits=50, max_deep_read=20, max_deep_read_seconds=120.0,
                   parallel_adapters=True)
```

## 7. Сценарий поиска: SimpleSearchProfile

Полный набор рычагов — через конструктор, без наследования:

```python
from nexusearch import SimpleSearchProfile
from nexusearch.hints import EMAIL_PATTERN, PHONE_PATTERN

profile = SimpleSearchProfile(
    # --- идентичность ---
    name="horeca_b2b",                      # обязательно; попадает в meta.profile
    description="HoReCa B2B suppliers",     # для роутера/LLM-классификации

    # --- расширение запросов ---
    query_templates=[                       # обязательно; каждый содержит {query}
        "{query} HoReCa опт поставка",
        "{query} оптовый поставщик прайс-лист",
    ],
    iter2_template='"{brand}"',             # итерация 2 по брендам из заголовков

    # --- география ---
    ignored_domains=["eda.ru", "pinterest."],   # чёрный список (см. семантику ниже)
    allowed_domains=["optom.ru"],               # белый список (если задан — только они)
    url_path_patterns=[r"/suppliers?", r"/catalog"],  # путь URL должен матчиться

    # --- deep-read ---
    deep_paths=("/", "/contacts", "/about"),    # какие страницы читать
    channels=("serp",),                          # какие каналы адаптеров допустимы

    # --- контент-политика ---
    stop_words=["рецепт", "как приготовить"],   # красные флаги → reject_page
    required_words=["прайс", "отгрузка"],       # зелёные флаги → accept_domain

    # --- извлечение фактов ---
    hint_patterns={"email": EMAIL_PATTERN, "phone": PHONE_PATTERN},
    int_hints=(),                                # поля, парсимые в int
)
```

Семантика `ignored_domains` / `allowed_domains` (одинаковая везде):

- `"x.com"` — точный хост **или поддомен** (`www.x.com`, `shop.x.com`);
  не матчит `prefixx.com`
- `"amazon."` — **с точкой на конце**: матчит метку в любом TLD
  (`amazon.com`, `amazon.co.uk`, ...)

Как шаблоны становятся запросами: `str.replace("{query}", query)` — поэтому
другие фигурные скобки в шаблоне безопасны. iter2: из заголовков хитов
вытаскиваются «брендовые» слова (3+ символа, до 4 слов) и подставляются в
`iter2_template`; дубликаты с iter1 выкидываются.

Валидация в конструкторе: пустое имя, шаблон без `{query}`, битый regex —
`ValueError` сразу, а не в рантайме.

`hint_patterns`: regex компилируется с `IGNORECASE`; берётся **первая группа**
`(...)`, если есть, иначе весь матч. Поля из `int_hints` дополнительно
парсятся в `int` (пробелы/запятые вырезаются).

## 8. Полный протокол SearchProfile

Нужен, когда декларативного сценария мало (LLM-планирование запросов,
своя формула confidence, динамические шаблоны):

```python
from typing import Any
from nexusearch import HintConfidence, PageSnippet, SearchHit

class MyProfile:
    name = "my-domain"
    ignored_domains = ("amazon.",)
    deep_paths = ("/", "/contacts")
    channels = ("serp", "llm_grounded")   # опционально; дефолт ("serp",)

    def plan_iter1(self, query: str, *, llm, max_queries: int) -> list[str]:
        # llm — ваш LlmJsonClient или None; верните ≤ max_queries запросов
        ...

    def plan_iter2(self, hits: list[SearchHit], *, already_used: list[str],
                   llm, max_queries: int) -> list[str]:
        ...

    def extract_hints(self, text: str, *, url: str) -> dict[str, Any]:
        ...

    def score_confidence(self, pages: list[PageSnippet], *, pages_with_hints: int,
                         has_any_hint: bool,
                         contributing_excerpt_len: int = 0) -> HintConfidence | None:
        ...
```

Плюс **опциональные duck-typed крючки** (подхватываются автоматически):

```python
    def reject_page(self, text: str, *, url: str) -> bool:
        """True → домен выбрасывается немедленно (стоп-слова)."""

    def accept_domain(self, texts: list[str], *, domain: str) -> bool:
        """False → домен выбрасывается в конце (нет обязательных слов)."""

    allowed_domains: Sequence[str] = ()       # hard geo-filter в client.search
    url_path_patterns: Sequence[str] = ()
    description: str = ""                      # для роутинга
```

Ошибки внутри крючков **fail-open**: логируются, поиск продолжается.

## 9. Адаптеры и каналы

| Адаптер | `channel` | Ключ | Примечание |
|---|---|---|---|
| `TavilyAdapter(api_key, *, attempts=3)` | `serp` | да | extra `tavily` |
| `BraveAdapter(api_key, *, timeout=8.0)` | `serp` | да | |
| `SerpApiAdapter(api_key, *, timeout=10.0)` | `serp` | да | ключ в query-string |
| `DuckDuckGoAdapter()` | `serp` | нет | библиотека + HTML-фолбэк |
| `LlmGroundedAdapter(llm)` | `llm_grounded` | ваш LLM | citations→hits, URL-gate |
| `DomainChannelAdapter(...)` | любой | через backend | декларативный канал |

Передача в клиент:

```python
from nexusearch import BraveAdapter, DuckDuckGoAdapter, TavilyAdapter

client = NexusSearchClient(
    profile=profile,
    adapters=[TavilyAdapter(tkey), BraveAdapter(bkey), DuckDuckGoAdapter()],
)
```

Если `adapters` не передан — строится `default_adapters()` из переданных
ключей (+ DDG всегда). Клиент дополнительно **фильтрует адаптеры по
`profile.channels`**: канал адаптера, не входящий в список профиля, не
вызывается.

### Свой канал без классов: DomainChannelAdapter

Обобщение «поиска по выставкам»: шаблоны запросов + фильтры поверх любого
SERP-бэкенда:

```python
from nexusearch import DomainChannelAdapter, DuckDuckGoAdapter

expo = DomainChannelAdapter(
    name="expo",
    query_templates=["{query} trade show exhibitors list"],
    allowed_domains=["10times.com", "expocentr.ru"],          # whitelist доменов
    path_patterns=[r"(?:^|[/._-])(expos?|exhibitors?)(?:[/._-]|$)"],  # и/или паттерны путей
    backend=DuckDuckGoAdapter(),   # по умолчанию DDG
    oversample=3,                  # запрашиваем в 3× больше, фильтруем
)
client = NexusSearchClient(profile=profile, adapters=[expo])
```

Хит проходит, если матчит **хотя бы один** из фильтров; дедуп по домену;
provenance переписывается на канал (`source_adapter="expo"`).

### LLM grounded search

`LlmGroundedAdapter` принимает любой объект с `generate_json(prompt) -> dict`
(Perplexity sonar, OpenAI + web_search, Gemini grounding). Жёсткий гейт:
кандидат становится хитом только с валидным https-URL, проходящим SSRF-проверку
и ignore-list; сущности без URL копятся в `adapter.discarded_entities`
(для отладки «что LLM нашла, но не смогла подкрепить ссылкой»).

### Свой адаптер (протокол)

```python
class MyAdapter:
    name = "my_engine"
    channel = "serp"

    def discover(self, query: str, *, max_results: int, iteration: int,
                 ignored_domains=()) -> tuple[list[SearchHit], bool]:
        # return (hits, attempted_ok); attempted_ok=True только после
        # успешного ответа вашего бэкенда
        ...
```

Падающий адаптер не роняет пайплайн: исключение логируется, адаптер считается
`([], False)`.

## 10. Контент-политика: отбраковка мусора

Двухступенчатая схема во время deep-read:

1. **`reject_page(text, *, url)`** — после каждой скачанной страницы.
   `True` → домен выбрасывается немедленно (экономим оставшиеся страницы).
2. **`accept_domain(texts, *, domain)`** — в конце, по ПОЛНЫМ текстам всех
   страниц (не по обрезанным excerpt'ам — обязательное слово может быть ниже
   капа). `False` → домен выбрасывается.

Отклонённые хиты не попадают в выдачу; счётчик — `meta.rejected_count`,
страница — `PageEvidence.rejected=True`. В `SimpleSearchProfile` обе ступени
задаются `stop_words` / `required_words` (подстрока, без учёта регистра).

## 11. Мульти-сценарий: роутинг

Когда сценариев несколько («поставщики», «выставки», «конкуренты»):

```python
from nexusearch import (
    HybridRouter, RoutedSearchClient, RouteRule, RuleRouter, SimpleSearchProfile,
)

router = HybridRouter(
    rules=[RouteRule(profile="expo", pattern=r"выставк|expo|exhibitor")],
    default="suppliers",
)

client = RoutedSearchClient(
    profiles={"suppliers": suppliers_profile, "expo": expo_profile},
    router=router,
    llm=my_llm,                # опционально: классификация при промахе правил
    brave_api_key="...",       # общие kwargs проксируются в каждый под-клиент
)

bundle = client.search("мебель выставка exhibitors")
bundle.meta.profile   # "expo" — куда сроутилось
client.route(query)   # только resolve сценария (для UI/отладки)
```

- `RuleRouter` — детерминированный, первое совпадение regex (IGNORECASE).
- `HybridRouter` — правила → LLM-классификация по `name: description`
  сценариев → default. Промах/ошибка/неизвестное имя → default.
- Каждый сценарий — свой `NexusSearchClient` со своей канал-фильтрацией.
- Роутер валидируется в конструкторе `RoutedSearchClient`: правило на
  неизвестный сценарий → `ValueError`.

## 12. Модели данных

Все модели — строгие pydantic (`extra="forbid"`).

```python
SearchHit:
    title, url, snippet, domain
    discovery_query          # какой запрос его нашёл
    iteration                # 1 или 2
    source_adapter           # "tavily" / "ddg" / "expo" / ...
    channel                  # "serp" / "llm_grounded" / ...
    grounding_score          # confidence из LLM-адаптера (0..1) или None
    page_evidence: PageEvidence | None

PageEvidence:
    domain, pages: list[PageSnippet(url, text_excerpt)]
    extracted: dict          # ваши hint-поля (email, phone, ...)
    sources: dict            # поле → URL, откуда извлечено
    hint_confidence          # "LOW" | "MEDIUM" | "HIGH" | None
    rejected: bool           # домен отклонён контент-политикой

SearchBundle:
    hits: list[SearchHit]
    meta: SearchMeta

SearchMeta:
    iterations_run, queries_used
    engines                  # адаптеры, реально ответившие (attempted)
    engines_with_hits        # адаптеры, давшие ≥1 хит в выдачу
    deep_read_count
    profile                  # имя сценария (и при роутинге)
    rejected_count           # сколько доменов выкинула контент-политика
    message                  # "No live search hits found." при пустом
```

## 13. Ops: лимиты, брейкеры, кэш, хуки, async

```python
from nexusearch import (
    Budget, CircuitBreaker, CostBudget, RateLimiter, RedisSearchCache, SearchCache,
)

limiter = RateLimiter(30, per_seconds=60.0)                 # токен-бакет, thread-safe
breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)
credits = CostBudget(max_credits=500)

adapter = BraveAdapter(key, rate_limiter=limiter, circuit_breaker=breaker)
# исчерпанный лимитер / открытый брейкер → ([], False), адаптер "не пытался"
# брейкер записывает итог вызова: failure только при реальном отказе
# credits.charge() → False, когда бюджет кончился (в своих адаптерах)

adapter.close()  # teardown: адаптеры переиспользуют один httpx.Client
```

Кэш (одинаковый формат ключа у обоих бэкендов):

```python
cache = SearchCache(ttl_seconds=900, max_entries=256)        # in-memory
cache = RedisSearchCache("redis://localhost:6379/0")         # extra redis
key = SearchCache.key(profile.name, query)
bundle = cache.get(key) or client.search(query)
cache.set(key, bundle)
```

Хуки (метрики/логи/трейсинг; ошибки хуков глушатся и логируются):

```python
class MyHooks:
    def on_search_start(self, query, options): ...
    def on_queries_planned(self, queries, *, iteration): ...
    def on_hit_discovered(self, hit): ...
    def on_search_end(self, bundle, *, elapsed_ms): ...
    def on_search_error(self, error, *, elapsed_ms): ...

client = NexusSearchClient(profile=..., hooks=[MyHooks()])
```

Async — та же конфигурация, `await client.search(...)`; sync-пайплайн
исполняется в worker-потоке (адаптеры синхронные):

```python
from nexusearch import AsyncNexusSearchClient
client = AsyncNexusSearchClient.from_env(profile=profile, hooks=[MyHooks()])
bundle = await client.search("query")
```

## 14. Интеграция в свой проект: чек-лист

1. **Зависимость**: git-source с тегом (`rev = "v0.5.2"`), extras по вкусу.
2. **Ключи**: поля в *вашем* settings (`BRAVE_API_KEY` и т.д.), передача явными
   kwargs — не рассчитывайте, что библиотека прочитает ваш env.
3. **Сценарий**: начните с `SimpleSearchProfile` (5–10 строк); полный
   `SearchProfile` — когда понадобится LLM-планирование.
4. **Клиент**: один долгоживущий клиент на сценарий (адаптеры переиспользуют
   HTTP-соединения); не создавайте клиент на каждый запрос, если не нужны
   разные ключи per-request.
5. **Guards**: `RateLimiter` на платные API, `CircuitBreaker` на нестабильные.
6. **Телеметрия**: читайте `meta` — `engines` vs `engines_with_hits`,
   `rejected_count`, `message`; хуки для Prometheus/логов.
7. **Тесты**: не дёргайте сеть — мокайте адаптеры (см. `tests/` в репо,
   например `test_profile_filter.py`); live smoke — отдельно и opt-in.
8. **Логирование**: библиотека пишет в `logging.getLogger("nexusearch.*")`;
   WARNING = «адаптер не смог», INFO = «роутинг/фильтры», DEBUG = детали.

Эталонная интеграция — потребитель S3-Nexus: свой settings → ключи в конструктор
→ `DomainChannelAdapter`-пресет для expo-канала → агент поверх `client.search()`.

## 15. Диагностика: почему пусто

| Симптом | Где смотреть | Типичная причина |
|---|---|---|
| `hits == []`, `meta.engines == []` | ключи/сеть | ни один адаптер не попытался: нет ключей, нет пакетов, канал профиля отфильтровал все адаптеры |
| `engines` непуст, `engines_with_hits` пуст | запросы | шаблоны слишком узкие; `ignored_domains` съедает всё |
| хиты есть до deep-read, в выдаче пусто | `meta.rejected_count` | контент-политика: `stop_words` срабатывают или `required_words` не найдены |
| `engines` есть, хитов мало | `max_hits`, дедуп | один домен = один хит; поднимите `max_iter1_queries` |
| всё медленно | `parallel_adapters=True` | последовательный обход × ретраи |
| лимитер «не работает» | — | экземпляр `RateLimiter` должен быть **общим** для всех адаптеров/запросов |

Быстрый дебаг: `logging.basicConfig(level=logging.INFO)` — видны роутинг,
geo-filter drops и reject-события.

## 16. Безопасность

- Deep-read: только https, DNS-pinning, только global IP (CGNAT
  `100.64.0.0/10` и прочие non-global отвергаются), кап тела 2 MiB,
  жёсткие дедлайны.
- Прокси — граница доверия: allowlist + DNS blocklist до отправки URL.
- Firecrawl — внепроцессный фетч: локальная DNS-проверка advisory (TOCTOU);
  отдельный httpx-клиент; отключается `allow_firecrawl=False`.
- SerpAPI: ключ в query-string → httpx-логгер приглушён до WARNING.
- LLM-адаптер: sanitize полей перед подстановкой в промпт, кандидаты без
  валидного URL не попадают в хиты.
- Политика репортов уязвимостей: [SECURITY.md](../SECURITY.md).

## 17. FAQ

**Q: Сколько ссылок выдаёт поиск?**
`NexusSearchOptions.max_hits` (дефолт 20). Хит = уникальный домен.

**Q: Можно без ключей?**
Да: `DuckDuckGoAdapter` бесплатный. Остальные адаптеры без ключей просто
вернут `([], False)`.

**Q: Чем `engines` отличается от `engines_with_hits`?**
Первое — «адаптер ответил», второе — «адаптер дал хиты». Расхождение =
адаптер жив, но его выдача не подошла (дедуп/фильтры).

**Q: Как добавить свой источник (каталог, выставки)?**
Либо декларативно — `DomainChannelAdapter` (шаблоны + фильтры), либо полный
протокол `DiscoveryAdapter`.

**Q: Хуки могут уронить поиск?**
Нет: исключения хуков логируются и подавляются (`HookPipeline` /
`emit_hooks_sync`).

**Q: Thread-safety?**
Клиенты и адаптеры не держат per-request состояния; общие `RateLimiter` /
`CircuitBreaker` / `CostBudget` / кэши — потокобезопасны. `parallel_adapters`
использует пул потоков, shared httpx.Client — документированно thread-safe.

**Q: Где граница «библиотека vs потребитель»?**
Библиотека: механика поиска, безопасность, телеметрия. Потребитель: доменные
данные (списки доменов, стоп-слова), ключи, оркестрация бизнес-логики.

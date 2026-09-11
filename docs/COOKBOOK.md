# Cookbook

Рецепты по использованию nexusearch.

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

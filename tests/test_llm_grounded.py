"""LlmGroundedAdapter tests: citations→hits only, no invented domains."""

from nexusearch.adapters_llm import LlmGroundedAdapter


class _FakeLlm:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def generate_json(self, prompt: str):
        self.prompts.append(prompt)
        return self.payload


def test_hits_only_with_valid_https_url():
    llm = _FakeLlm({
        "candidates": [
            {"name": "Real Co", "url": "https://realco.example/wholesale", "why": "makes X", "confidence": 0.8},
            {"name": "NoUrl Co", "why": "no url provided"},
            {"name": "Http Co", "url": "http://insecure.example/", "why": "http only"},
            {"name": "Garbage Co", "url": "not-a-url", "why": ""},
            {"name": "CGNAT", "url": "https://100.64.0.1/", "why": "blocked ip"},
        ]
    })
    adapter = LlmGroundedAdapter(llm, name="llm_fake")
    hits, used = adapter.discover("cnc suppliers")
    assert used is True
    assert len(hits) == 1
    hit = hits[0]
    assert hit.url == "https://realco.example/wholesale"
    assert hit.domain == "realco.example"
    assert hit.channel == "llm_grounded"
    assert hit.source_adapter == "llm_fake"
    assert hit.grounding_score == 0.8
    # discarded entities tracked, never in hits
    assert len(adapter.discarded_entities) == 4
    assert all("url" in d for d in adapter.discarded_entities)


def test_ignored_domains_filtered():
    llm = _FakeLlm({
        "candidates": [
            {"name": "Amazon listing", "url": "https://amazon.com/dp/1"},
            {"name": "Maker", "url": "https://maker.example/"},
        ]
    })
    adapter = LlmGroundedAdapter(llm)
    hits, _ = adapter.discover("q", ignored_domains=("amazon.com",))
    assert [h.domain for h in hits] == ["maker.example"]
    assert adapter.discarded_entities[0]["reason"] == "ignored_domain"


def test_no_llm_no_hits():
    adapter = LlmGroundedAdapter(None)
    hits, used = adapter.discover("q")
    assert hits == []
    assert used is False


def test_llm_exception_returns_not_used():
    class _Boom:
        def generate_json(self, prompt: str):
            raise RuntimeError("api down")

    hits, used = LlmGroundedAdapter(_Boom()).discover("q")
    assert hits == []
    assert used is False


def test_malformed_json_string_recovered():
    llm = _FakeLlm('prefix {"candidates": [{"name": "X", "url": "https://x.example/"}]} suffix')
    hits, used = LlmGroundedAdapter(llm).discover("q")
    assert used is True
    assert [h.domain for h in hits] == ["x.example"]

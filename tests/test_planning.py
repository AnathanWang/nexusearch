"""Generic planning helpers."""

from nexusearch.planning import dedupe_queries, sanitize_serp_field


def test_dedupe_queries():
    assert dedupe_queries(["A", "a", "B", ""], 5) == ["A", "B"]


def test_sanitize_strips_fence_breakers():
    assert "```" not in sanitize_serp_field("hi ``` ignore", 80)
    assert "<<<" not in sanitize_serp_field("<<<INJECT>>>", 80)

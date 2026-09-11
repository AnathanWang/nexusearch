"""SimpleSearchProfile tests (declarative search scenario)."""

import pytest

from nexusearch.hints import EMAIL_PATTERN, PHONE_PATTERN
from nexusearch.models import PageSnippet, SearchHit
from nexusearch.profile import SearchProfile
from nexusearch.simple_profile import SimpleSearchProfile


def _profile(**kw) -> SimpleSearchProfile:
    defaults = {
        "name": "horeca_b2b",
        "query_templates": [
            "{query} HoReCa опт поставка",
            "{query} оптовый поставщик прайс-лист",
            "{query} дистрибьютор продуктов для ресторанов",
        ],
    }
    defaults.update(kw)
    return SimpleSearchProfile(**defaults)


def test_satisfies_search_profile_protocol():
    assert isinstance(_profile(), SearchProfile)


def test_iter1_templates_expand_user_query():
    p = _profile()
    queries = p.plan_iter1("мраморная говядина", llm=None, max_queries=5)
    assert queries == [
        "мраморная говядина HoReCa опт поставка",
        "мраморная говядина оптовый поставщик прайс-лист",
        "мраморная говядина дистрибьютор продуктов для ресторанов",
    ]


def test_iter1_respects_max_queries_and_dedupes():
    p = _profile(query_templates=["{query} опт", "{query} опт", "{query} завод"])
    assert p.plan_iter1("кирпич", llm=None, max_queries=5) == ["кирпич опт", "кирпич завод"]
    assert p.plan_iter1("кирпич", llm=None, max_queries=1) == ["кирпич опт"]


def test_iter2_builds_brand_queries_and_skips_used():
    p = _profile(iter2_template='"{brand}" прайс')
    hits = [
        SearchHit(title="Мясной Двор — оптовые поставки", url="https://a.example/", domain="a.example"),
        SearchHit(title="!!", url="https://b.example/", domain="b.example"),
        SearchHit(title="ОвощБаза опт", url="https://c.example/", domain="c.example"),
    ]
    out = p.plan_iter2(hits, already_used=['"Мясной Двор" прайс'], llm=None, max_queries=3)
    assert '"Мясной Двор" прайс' not in out  # already used
    assert any("ОвощБаза" in q for q in out)
    assert all(q.startswith('"') for q in out)


def test_config_validation():
    with pytest.raises(ValueError, match="non-empty name"):
        _profile(name=" ")
    with pytest.raises(ValueError, match="at least one query template"):
        _profile(query_templates=[])
    with pytest.raises(ValueError, match="\\{query\\}"):
        _profile(query_templates=["опт без плейсхолдера"])
    with pytest.raises(ValueError, match="\\{brand\\}"):
        _profile(iter2_template="no placeholder")
    with pytest.raises(ValueError, match="invalid hint pattern"):
        _profile(hint_patterns={"bad": "([unclosed"})
    with pytest.raises(ValueError, match="invalid url_path_pattern"):
        _profile(url_path_patterns=["([unclosed"])


def test_reject_page_stop_words():
    p = _profile(stop_words=["рецепт", "положить в корзину"])
    assert p.reject_page("Как приготовить: рецепт стейка", url="https://eda.ru/x") is True
    assert p.reject_page("Оптовые поставки мяса", url="https://opt.example/") is False
    assert _profile().reject_page("рецепт", url="https://x.example/") is False  # no stop words


def test_accept_domain_required_words():
    p = _profile(required_words=["прайс", "отгрузка"])
    assert p.accept_domain(["Оптовая база. Скачать прайс."], domain="a.example") is True
    assert p.accept_domain(["Отгрузка фурами по России"], domain="a.example") is True
    assert p.accept_domain(["История компании", "О нас"], domain="a.example") is False
    assert _profile().accept_domain(["anything"], domain="a.example") is True  # not configured


def test_extract_hints_group_vs_whole_match():
    p = _profile(
        hint_patterns={
            "email": EMAIL_PATTERN,
            "exhibitor_count": r"(\d[\d,]*)\+?\s*exhibitors?",
        },
        int_hints=("exhibitor_count",),
    )
    hints = p.extract_hints(
        "Contact us at sales@meat-base.ru! Over 1,200 exhibitors this year.", url="https://x.example/"
    )
    assert hints["email"] == "sales@meat-base.ru"
    assert hints["exhibitor_count"] == 1200


def test_extract_hints_phone_and_absent_fields():
    p = _profile(hint_patterns={"phone": PHONE_PATTERN})
    hints = p.extract_hints("Звоните: +7 (495) 123-45-67 доб. 5", url="https://x.example/")
    assert hints["phone"].startswith("+7")
    assert "email" not in hints
    assert p.extract_hints("no contacts here", url="https://x.example/") == {}


def test_extract_hints_bad_int_skipped():
    p = _profile(hint_patterns={"n": r"count:\s*(\w+)"}, int_hints=("n",))
    assert p.extract_hints("count: many", url="https://x.example/") == {}
    assert p.extract_hints("count: 42", url="https://x.example/") == {"n": 42}


def test_score_confidence_policy():
    p = _profile()
    assert p.score_confidence([], pages_with_hints=0, has_any_hint=False) is None
    one = [PageSnippet(url="https://a.example/", text_excerpt="x")]
    assert p.score_confidence(one, pages_with_hints=1, has_any_hint=True) == "LOW"
    two = one + [PageSnippet(url="https://a.example/about", text_excerpt="y")]
    assert p.score_confidence(two, pages_with_hints=2, has_any_hint=True) == "HIGH"


def test_geo_config_exposed_for_client_filter():
    p = _profile(
        allowed_domains=["B2B-Center.ru", " supl.biz "],
        url_path_patterns=[r"/(wholesale|opt|b2b)"],
    )
    assert p.allowed_domains == ("b2b-center.ru", "supl.biz")  # normalized
    assert p.url_path_patterns == (r"/(wholesale|opt|b2b)",)

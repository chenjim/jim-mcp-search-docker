"""Unit tests for query-type routing."""
import pytest

from src import routing


@pytest.mark.parametrize(
    "query,expected",
    [
        ("个人养老金 缴费上限 2026 政策", "zh_policy"),
        ("2026年11月 人工智能 监管 新规 发布", "zh_policy"),
        ("2026年11月 中国 CPI 同比 数据", "zh_data"),
        ("公众号 排版 教程 字号 行距", "zh_community"),
        ("京杭大运河 全长 起点 终点", "zh_default"),
        ("PostgreSQL 18 release notes", "en_docs"),
        ("Playwright browser_navigate API documentation", "en_docs"),
        ("best budget mechanical keyboard 2026 review", "en_review"),
        ("Mac Studio M5 Ultra specs memory bandwidth", "en_docs"),
        ("DeepSeek V4 Flash pricing per million tokens", "en_review"),
        ("what happened in ai last week", "en_default"),
    ],
)
def test_classify(query, expected):
    assert routing.classify(query) == expected


def test_providers_for_puts_deepseek_first_for_chinese_policy():
    assert routing.providers_for("个人养老金 政策")[0] == "deepseek"


def test_providers_for_puts_parallel_first_for_english_docs():
    assert routing.providers_for("PostgreSQL 18 release notes")[0] == "parallel"


def test_route_members_are_known_providers():
    known = {"brave", "tavily", "parallel", "baidu", "deepseek", "firecrawl"}
    for name, members in routing.ROUTES.items():
        assert members, name
        assert set(members) <= known, name

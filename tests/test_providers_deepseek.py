"""Unit tests for the DeepSeek Search provider."""
from datetime import date, timedelta

import pytest

from src.providers import ProviderError, ProviderQuotaError
from src.providers import deepseek


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _enable(monkeypatch, key="test-key"):
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", key)


def test_search_returns_empty_without_key(monkeypatch):
    _enable(monkeypatch, key=None)
    assert deepseek.search("q") == []


def test_search_parses_results_and_falls_back_to_summary(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse(
            {
                "results": [
                    {"title": "A", "url": "https://a.com", "snippet": "short", "summary": "long"},
                    {"title": "B", "url": "https://b.com", "snippet": "", "summary": "s" * 700},
                    {"title": "no url", "url": "", "snippet": "x"},
                    "not-a-dict",
                ]
            }
        )

    monkeypatch.setattr(deepseek.requests, "post", fake_post)
    results = deepseek.search("what is x", count=5)

    assert [r.url for r in results] == ["https://a.com", "https://b.com"]
    assert results[0].title == "A"
    assert results[0].snippet == "short"
    # snippet 为空时回退 summary，并截断到 500
    assert len(results[1].snippet) == deepseek.MAX_SNIPPET_CHARS

    assert captured["url"] == deepseek.ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["count"] == 5
    # 只当搜索源用：绝不取正文
    assert captured["payload"]["need_content"] is False


def test_search_clamps_count(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(deepseek.requests, "post", fake_post)
    assert deepseek.search("q", count=100) == []
    assert captured["payload"]["count"] == deepseek.MAX_COUNT


@pytest.mark.parametrize(
    "freshness,days",
    [("pd", 1), ("pw", 7), ("pm", 30), ("py", 365)],
)
def test_search_maps_freshness_to_after_operator(monkeypatch, freshness, days):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(deepseek.requests, "post", fake_post)
    deepseek.search("latest model", freshness=freshness)

    expected = (date.today() - timedelta(days=days)).isoformat()
    assert captured["payload"]["query"] == f"latest model after:{expected}"


def test_search_keeps_existing_time_operator(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(deepseek.requests, "post", fake_post)
    deepseek.search("q after:2025-01-01", freshness="pw")
    assert captured["payload"]["query"] == "q after:2025-01-01"


def test_search_truncates_query_to_limit_including_operator(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(deepseek.requests, "post", fake_post)
    deepseek.search("x" * 500, freshness="pw")

    query = captured["payload"]["query"]
    assert len(query) <= deepseek.MAX_QUERY_CHARS
    assert " after:" in query


def test_search_raises_quota_error_on_429(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        deepseek.requests, "post", lambda *a, **k: _FakeResponse({}, status=429)
    )
    with pytest.raises(ProviderQuotaError):
        deepseek.search("q")


def test_search_raises_provider_error_on_transient_http_error(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        deepseek.requests, "post", lambda *a, **k: _FakeResponse({}, status=422)
    )
    with pytest.raises(ProviderError):
        deepseek.search("q")

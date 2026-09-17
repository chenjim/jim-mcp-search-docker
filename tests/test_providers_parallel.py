"""Unit tests for the Parallel AI search provider."""
import pytest

from src.providers import ProviderError, ProviderQuotaError
from src.providers import parallel


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
    monkeypatch.setattr(parallel.config, "PARALLEL_API_KEY", key)


def test_search_returns_empty_without_key(monkeypatch):
    _enable(monkeypatch, key=None)
    assert parallel.search("q") == []


def test_search_parses_results_and_merges_excerpts(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse(
            {
                "search_id": "s1",
                "results": [
                    {
                        "url": "https://a.com",
                        "title": "A",
                        "excerpts": ["first part", "second part"],
                    },
                    {"url": "https://b.com", "title": None, "excerpts": []},
                ],
            }
        )

    monkeypatch.setattr(parallel.requests, "post", fake_post)
    results = parallel.search("what is x", count=5, freshness="pw")

    assert [r.url for r in results] == ["https://a.com", "https://b.com"]
    assert results[0].title == "A"
    assert results[0].snippet == "first part second part"
    assert results[1].title == ""
    assert results[1].snippet is None

    assert captured["url"] == parallel.ENDPOINT
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["payload"]["search_queries"] == ["what is x"]
    assert captured["payload"]["advanced_settings"]["max_results"] == 5
    assert captured["payload"]["mode"] == parallel.config.PARALLEL_SEARCH_MODE


def test_search_truncates_query_and_clamps_count(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(parallel.requests, "post", fake_post)
    parallel.search("x" * 500, count=100)

    assert len(captured["payload"]["search_queries"][0]) == parallel.MAX_QUERY_CHARS
    assert captured["payload"]["advanced_settings"]["max_results"] == 20


def test_search_raises_provider_error_on_transient_http_error(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        parallel.requests, "post", lambda *a, **k: _FakeResponse({}, status=422)
    )
    with pytest.raises(ProviderError):
        parallel.search("q")


def test_search_raises_quota_error_on_429(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        parallel.requests, "post", lambda *a, **k: _FakeResponse({}, status=429)
    )
    with pytest.raises(ProviderQuotaError):
        parallel.search("q")

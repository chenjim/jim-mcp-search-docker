"""Unit tests for the Baidu Qianfan AI Search provider."""
import pytest

from src.providers import ProviderError, ProviderQuotaError
from src.providers import baidu


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
    monkeypatch.setattr(baidu.config, "BAIDU_API_KEY", key)


def test_search_returns_empty_without_key(monkeypatch):
    _enable(monkeypatch, key=None)
    assert baidu.search("q") == []


def test_search_parses_references_and_skips_non_web(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse(
            {
                "request_id": "r1",
                "references": [
                    {
                        "type": "web",
                        "title": "A",
                        "url": "https://a.com",
                        "content": "snippet a",
                        "date": "2026-09-17",
                    },
                    {"type": "image", "title": "img", "url": "http://img.example/x.jpg"},
                    {"type": "web", "title": "no url", "content": "x"},
                    {"type": "web", "title": None, "url": "https://b.com", "content": None},
                ],
            }
        )

    monkeypatch.setattr(baidu.requests, "post", fake_post)
    results = baidu.search("what is x", count=5, freshness="pm")

    assert [r.url for r in results] == ["https://a.com", "https://b.com"]
    assert results[0].title == "A"
    assert results[0].snippet == "snippet a"
    assert results[1].title == ""
    assert results[1].snippet is None

    assert captured["url"] == baidu.ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "what is x"}]
    assert captured["payload"]["search_source"] == "baidu_search_v2"
    assert captured["payload"]["resource_type_filter"] == [{"type": "web", "top_k": 5}]
    assert captured["payload"]["search_recency_filter"] == "month"
    assert captured["payload"]["stream"] is False
    # 当搜索源用时绝不传 model（否则会额外产生大模型费用）
    assert "model" not in captured["payload"]


def test_search_clamps_top_k_and_truncates_snippet(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse(
            {"references": [{"type": "web", "title": "t", "url": "https://a.com", "content": "z" * 900}]}
        )

    monkeypatch.setattr(baidu.requests, "post", fake_post)
    results = baidu.search("q", count=100, freshness="pd")

    # baidu_search_v2 web top_k 上限 20；freshness pd 只能落到 week（百度无 24h 档）
    assert captured["payload"]["resource_type_filter"] == [{"type": "web", "top_k": 20}]
    assert captured["payload"]["search_recency_filter"] == "week"
    assert len(results[0].snippet) == 500


def test_search_omits_recency_for_unknown_freshness(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"references": []})

    monkeypatch.setattr(baidu.requests, "post", fake_post)
    assert baidu.search("q", freshness="bogus") == []
    assert "search_recency_filter" not in captured["payload"]


def test_search_raises_quota_error_on_402(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(baidu.requests, "post", lambda *a, **k: _FakeResponse({}, status=402))
    with pytest.raises(ProviderQuotaError):
        baidu.search("q")


def test_search_raises_quota_error_on_auth_code(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        baidu.requests,
        "post",
        lambda *a, **k: _FakeResponse(
            {"code": 216003, "message": "Authentication error: Fail to parse apikey authorization"}
        ),
    )
    with pytest.raises(ProviderQuotaError):
        baidu.search("q")


def test_search_raises_provider_error_on_other_code(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        baidu.requests, "post", lambda *a, **k: _FakeResponse({"code": 400, "message": "bad param"})
    )
    with pytest.raises(ProviderError):
        baidu.search("q")

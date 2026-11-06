"""Unit tests for the DeepSeek Fetch scraper."""
import pytest

from src.scrapers import deepseek


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_scrape_without_key_returns_error(monkeypatch):
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", None)
    result = deepseek.scrape("https://a.com")
    assert result.markdown is None
    assert "not configured" in result.error


def test_scrape_returns_markdown(monkeypatch):
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek.config, "MIN_CONTENT_LENGTH", 10)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse({"title": "T", "content": "# article\n\nbody text"})

    monkeypatch.setattr(deepseek.requests, "post", fake_post)
    result = deepseek.scrape("https://a.com/post")

    assert result.source == "deepseek"
    assert result.markdown.startswith("# article")
    assert captured["url"] == deepseek.ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"] == {"url": "https://a.com/post"}


def test_scrape_rejects_short_content(monkeypatch):
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek.config, "MIN_CONTENT_LENGTH", 200)
    monkeypatch.setattr(
        deepseek.requests,
        "post",
        lambda *a, **k: _FakeResponse({"title": "T", "content": "too short"}),
    )
    result = deepseek.scrape("https://a.com")
    assert result.markdown is None
    assert "too short" in result.error.lower() or "Content too short" in result.error


def test_scrape_returns_error_on_http_failure(monkeypatch):
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        deepseek.requests, "post", lambda *a, **k: _FakeResponse({}, status=404)
    )
    result = deepseek.scrape("https://a.com/missing")
    assert result.markdown is None
    assert result.error

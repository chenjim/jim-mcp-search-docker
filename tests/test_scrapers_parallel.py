"""Unit tests for the Parallel AI Extract scraper and its place in the scrape chain."""
from src import service
from src.scrapers import ScrapeResult
from src.scrapers import parallel as parallel_scraper


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
    monkeypatch.setattr(parallel_scraper.config, "PARALLEL_API_KEY", key)


def test_scrape_returns_error_without_key(monkeypatch):
    _enable(monkeypatch, key=None)
    result = parallel_scraper.scrape("https://a.com")
    assert result.markdown is None
    assert "PARALLEL_API_KEY" in result.error


def test_scrape_parses_full_content_and_caps_chars(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(parallel_scraper.config, "MAX_CHARS", 123)
    captured = {}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse({"results": [{"url": "https://a.com", "full_content": "# " + "x" * 400}]})

    monkeypatch.setattr(parallel_scraper.requests, "post", fake_post)
    result = parallel_scraper.scrape("https://a.com")

    assert result.source == "parallel"
    assert result.markdown.startswith("# ")
    assert captured["url"] == parallel_scraper.EXTRACT_ENDPOINT
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["payload"]["urls"] == ["https://a.com"]
    assert captured["payload"]["advanced_settings"]["full_content"] == {"max_chars_per_result": 123}


def test_scrape_full_content_true_when_unlimited(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(parallel_scraper.config, "MAX_CHARS", 0)
    captured = {}

    def fake_post(url, headers=None, json=None, proxies=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse({"results": [{"url": "https://a.com", "full_content": "y" * 400}]})

    monkeypatch.setattr(parallel_scraper.requests, "post", fake_post)
    parallel_scraper.scrape("https://a.com")

    assert captured["payload"]["advanced_settings"]["full_content"] is True


def test_scrape_returns_error_on_short_content(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        parallel_scraper.requests, "post",
        lambda *a, **k: _FakeResponse({"results": [{"url": "https://a.com", "full_content": "short"}]}),
    )
    result = parallel_scraper.scrape("https://a.com")
    assert result.markdown is None
    assert "too short" in result.error.lower()


def test_scrape_reports_api_error(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        parallel_scraper.requests, "post",
        lambda *a, **k: _FakeResponse({"results": [], "errors": [{"url": "https://a.com", "error_type": "fetch_error"}]}),
    )
    result = parallel_scraper.scrape("https://a.com")
    assert result.markdown is None
    assert "fetch_error" in result.error


def test_scrape_chain_prefers_parallel_over_firecrawl(monkeypatch):
    firecrawl_called = False

    def fake_firecrawl(url):
        nonlocal firecrawl_called
        firecrawl_called = True
        return ScrapeResult(markdown="fc", source="firecrawl")

    monkeypatch.setattr(service.jina_scraper, "scrape", lambda url: ScrapeResult(markdown=None, source="jina", error="short"))
    monkeypatch.setattr(service.parallel_scraper, "scrape", lambda url: ScrapeResult(markdown="parallel content", source="parallel"))
    monkeypatch.setattr(service.firecrawl_scraper, "scrape", fake_firecrawl)

    result = service.scrape_one("https://a.com")
    assert result.source == "parallel"
    assert result.markdown == "parallel content"
    assert not firecrawl_called


def test_scrape_chain_falls_back_to_firecrawl_when_parallel_fails(monkeypatch):
    monkeypatch.setattr(service.jina_scraper, "scrape", lambda url: ScrapeResult(markdown="环境异常", source="jina"))
    monkeypatch.setattr(service.parallel_scraper, "scrape", lambda url: ScrapeResult(markdown=None, source="parallel", error="no key"))
    monkeypatch.setattr(service.firecrawl_scraper, "scrape", lambda url: ScrapeResult(markdown="fc content", source="firecrawl"))

    result = service.scrape_one("https://a.com")
    assert result.source == "firecrawl"
    assert result.markdown == "fc content"

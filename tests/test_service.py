"""Unit tests for src.service (search/scrape core)."""
import pytest

from src import service
from src.providers import SearchResult
from src.scrapers import ScrapeResult


def _provider(name, results):
    def _fn(query, count=10, freshness=None):
        return results

    return (name, _fn)


def test_search_dedups_and_keeps_snippet(monkeypatch):
    results = [
        SearchResult(title="A", url="https://a.com/x/", snippet="sa"),
        SearchResult(title="A dup", url="https://a.com/x", snippet="dup"),
        SearchResult(title="B", url="https://b.com", snippet="sb"),
    ]
    monkeypatch.setattr(service, "_provider_chain", lambda: [_provider("brave", results)])

    out = service.search("q", top=10)
    assert out["search_chain_used"] == ["brave"]
    assert [r["url"] for r in out["results"]] == ["https://a.com/x/", "https://b.com"]
    assert out["results"][0]["snippet"] == "sa"
    assert out["metadata"]["total_results"] == 2


def test_search_falls_back_to_next_provider(monkeypatch):
    chain = [_provider("brave", []), _provider("tavily", [SearchResult(title="T", url="https://t.com")])]
    monkeypatch.setattr(service, "_provider_chain", lambda: chain)

    out = service.search("q")
    assert out["search_chain_used"] == ["tavily"]
    assert out["results"][0]["url"] == "https://t.com"


def test_search_raises_when_all_fail(monkeypatch):
    def boom(query, count=10, freshness=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(service, "_provider_chain", lambda: [("brave", boom)])
    with pytest.raises(service.SearchError):
        service.search("q")


def test_scrape_one_uses_jina_and_truncates(monkeypatch):
    monkeypatch.setattr(
        service.jina_scraper, "scrape", lambda url: ScrapeResult(markdown="x" * 50, source="jina")
    )
    result = service.scrape_one("https://a.com", max_chars=10)
    assert result.source == "jina"
    assert result.markdown == "x" * 10


def test_scrape_one_falls_back_to_firecrawl(monkeypatch):
    monkeypatch.setattr(
        service.jina_scraper, "scrape",
        lambda url: ScrapeResult(markdown=None, source="jina", error="short"),
    )
    monkeypatch.setattr(
        service.firecrawl_scraper, "scrape",
        lambda url: ScrapeResult(markdown="fc content", source="firecrawl"),
    )
    result = service.scrape_one("https://a.com")
    assert result.source == "firecrawl"
    assert result.markdown == "fc content"


def test_scrape_urls_isolates_single_failure(monkeypatch):
    def fake_scrape(url, max_chars=None):
        if url.endswith("boom"):
            raise RuntimeError("kaboom")
        return ScrapeResult(markdown="ok", source="jina")

    monkeypatch.setattr(service, "scrape_one", fake_scrape)
    out = service.scrape_urls(["https://a.com", "https://boom", "https://c.com"])

    assert [r["status"] for r in out["results"]] == ["ok", "failed", "ok"]
    assert out["results"][1]["error"] == "kaboom"
    assert out["metadata"] == {"scraped_count": 2, "failed_count": 1}


def test_scrape_urls_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(service, "scrape_one", lambda url, max_chars=None: ScrapeResult())
    out = service.scrape_urls([])
    assert out["results"] == []
    assert out["metadata"] == {"scraped_count": 0, "failed_count": 0}


def test_scrape_urls_caps_url_count(monkeypatch):
    monkeypatch.setattr(
        service, "scrape_one", lambda url, max_chars=None: ScrapeResult(markdown="x", source="jina")
    )
    urls = [f"https://e.com/{i}" for i in range(service.MAX_SCRAPE_URLS + 5)]
    out = service.scrape_urls(urls)
    assert len(out["results"]) == service.MAX_SCRAPE_URLS


def test_search_clamps_top(monkeypatch):
    results = [SearchResult(title=f"T{i}", url=f"https://e.com/{i}") for i in range(50)]
    monkeypatch.setattr(service, "_provider_chain", lambda: [_provider("brave", results)])
    out = service.search("q", top=999)
    assert len(out["results"]) == service.MAX_RESULTS


def test_scrape_urls_aggregates_and_preserves_order(monkeypatch):
    def fake_scrape(url, max_chars=None):
        if url.endswith("bad"):
            return ScrapeResult(markdown=None, source="firecrawl", error="failed")
        return ScrapeResult(markdown=f"content:{url}", source="jina")

    monkeypatch.setattr(service, "scrape_one", fake_scrape)
    out = service.scrape_urls(["https://a.com", "https://bad", "https://c.com"], concurrency=2)

    assert [r["url"] for r in out["results"]] == ["https://a.com", "https://bad", "https://c.com"]
    assert out["results"][1]["status"] == "failed"
    assert out["results"][1]["scrape_source"] is None
    assert out["results"][1]["error"] == "failed"
    assert out["metadata"] == {"scraped_count": 2, "failed_count": 1}
    assert out["scrape_chain_used"] == ["jina"]

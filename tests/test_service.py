"""Unit tests for src.service (search/scrape core)."""
import pytest

from src import config, service
from src.providers import SearchResult
from src.scrapers import ScrapeResult
from src.scrapers import direct as direct_scraper


@pytest.fixture(autouse=True)
def _no_deepseek_by_default(monkeypatch):
    """默认关掉 DeepSeek：其余用例不应因为本机存在 key 而发真实请求。"""
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", None)


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


def test_scrape_one_wechat_goes_direct(monkeypatch):
    jina_called = False

    def fake_jina(url):
        nonlocal jina_called
        jina_called = True
        return ScrapeResult(markdown="jina", source="jina")

    monkeypatch.setattr(service.jina_scraper, "scrape", fake_jina)
    monkeypatch.setattr(
        service.direct_scraper, "scrape",
        lambda url: ScrapeResult(markdown="wx content", source="direct"),
    )
    result = service.scrape_one("https://mp.weixin.qq.com/s/abc")
    assert result.source == "direct"
    assert result.markdown == "wx content"
    assert not jina_called


def test_scrape_one_blocked_marker_falls_back(monkeypatch):
    monkeypatch.setattr(
        service.jina_scraper, "scrape",
        lambda url: ScrapeResult(markdown="环境异常，完成验证后即可继续访问", source="jina"),
    )
    monkeypatch.setattr(
        service.firecrawl_scraper, "scrape",
        lambda url: ScrapeResult(markdown="fc content", source="firecrawl"),
    )
    result = service.scrape_one("https://a.com")
    assert result.source == "firecrawl"
    assert result.markdown == "fc content"


def test_scrape_one_falls_back_to_direct(monkeypatch):
    monkeypatch.setattr(
        service.jina_scraper, "scrape",
        lambda url: ScrapeResult(markdown="环境异常", source="jina"),
    )
    monkeypatch.setattr(
        service.firecrawl_scraper, "scrape",
        lambda url: ScrapeResult(markdown=None, source="firecrawl", error="short"),
    )
    monkeypatch.setattr(
        service.direct_scraper, "scrape",
        lambda url: ScrapeResult(markdown="direct content", source="direct"),
    )
    result = service.scrape_one("https://a.com")
    assert result.source == "direct"
    assert result.markdown == "direct content"


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


def test_direct_extracts_js_content():
    html = (
        '<html><head><title>t</title></head><body>'
        '<div id="js_content"><div>foo</div><p>bar &nbsp;baz</p></div>'
        '<div class="rich_media_tool">ignored</div>'
        '</body></html>'
    )
    assert direct_scraper._extract_article(html) == "foo bar baz"


def test_direct_falls_back_to_full_page():
    html = "<html><body><p>plain content</p><script>var x=1;</script></body></html>"
    assert direct_scraper._extract_article(html) == "plain content"


def test_direct_marks_antibot_page_as_failed(monkeypatch):
    class FakeResp:
        text = '<div id="js_content">环境异常，完成验证后即可继续访问</div>'
        url = "https://mp.weixin.qq.com/s/abc"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        direct_scraper.requests, "get",
        lambda *a, **k: FakeResp(),
    )
    result = direct_scraper.scrape("https://mp.weixin.qq.com/s/abc")
    assert result.markdown is None
    assert result.error == "Blocked by anti-scraping page"


def test_search_records_query_attempts_and_results(monkeypatch):
    captured = []
    monkeypatch.setattr(
        service.recorder, "record", lambda kind, payload: captured.append((kind, payload))
    )
    chain = [
        _provider("brave", []),
        _provider("deepseek", [SearchResult(title="D", url="https://d.com", snippet="sd")]),
    ]
    monkeypatch.setattr(service, "_provider_chain", lambda: chain)

    service.search("apple results", top=5, freshness="pw")

    assert len(captured) == 1
    kind, entry = captured[0]
    assert kind == "search"
    assert entry["query"] == "apple results"
    assert entry["freshness"] == "pw"
    assert entry["chain_used"] == ["deepseek"]
    assert [a["provider"] for a in entry["attempts"]] == ["brave", "deepseek"]
    assert entry["attempts"][0]["count"] == 0
    assert entry["result_count"] == 1
    assert entry["results"][0] == {
        "rank": 1,
        "title": "D",
        "url": "https://d.com",
        "snippet": "sd",
    }


def test_search_records_failure_with_error(monkeypatch):
    captured = []
    monkeypatch.setattr(
        service.recorder, "record", lambda kind, payload: captured.append(payload)
    )

    def boom(query, count=10, freshness=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(service, "_provider_chain", lambda: [("brave", boom)])
    with pytest.raises(service.SearchError):
        service.search("q")

    assert captured[0]["result_count"] == 0
    assert "nope" in captured[0]["error"]


def test_scrape_one_prefers_deepseek_after_jina(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    parallel_called = False

    def fake_parallel(url):
        nonlocal parallel_called
        parallel_called = True
        return ScrapeResult(markdown="parallel", source="parallel")

    monkeypatch.setattr(
        service.jina_scraper, "scrape",
        lambda url: ScrapeResult(markdown=None, source="jina", error="blocked"),
    )
    monkeypatch.setattr(
        service.deepseek_scraper, "scrape",
        lambda url: ScrapeResult(markdown="ds content", source="deepseek"),
    )
    monkeypatch.setattr(service.parallel_scraper, "scrape", fake_parallel)

    result = service.scrape_one("https://a.com")
    assert result.source == "deepseek"
    assert result.markdown == "ds content"
    assert not parallel_called


def test_scrape_urls_records_each_url(monkeypatch):
    captured = []
    monkeypatch.setattr(
        service.recorder, "record", lambda kind, payload: captured.append((kind, payload))
    )
    monkeypatch.setattr(
        service, "scrape_one",
        lambda url, max_chars=None: ScrapeResult(markdown="x" * 10, source="jina"),
    )

    service.scrape_urls(["https://a.com"])

    assert captured[0][0] == "scrape"
    assert captured[0][1]["url"] == "https://a.com"
    assert captured[0][1]["status"] == "ok"
    assert captured[0][1]["source"] == "jina"
    assert captured[0][1]["chars"] == 10

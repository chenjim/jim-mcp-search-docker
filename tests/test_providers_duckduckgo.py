"""Unit tests for the DuckDuckGo html-endpoint provider."""
import pytest

from src.providers import ProviderError, ProviderQuotaError
from src.providers import duckduckgo


@pytest.fixture(autouse=True)
def _reset_cooldown():
    duckduckgo._blocked_until = 0.0
    yield
    duckduckgo._blocked_until = 0.0


SAMPLE_HTML = """
<html><body>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://www.postgresql.org/docs/release/18/">Release Notes - PostgreSQL</a>
  </h2>
  <a class="result__snippet" href="https://www.postgresql.org/docs/release/18/">PostgreSQL 18 <b>release</b> notes.</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpost&amp;rut=abc">Example</a>
  </h2>
</div>
<a class="result__a" href="">No title link</a>
</body></html>
"""


class _FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_post(captured, text=SAMPLE_HTML, status=200):
    def _post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return _FakeResponse(text, status)

    return _post


def test_search_parses_titles_snippets_and_unwraps_redirect(monkeypatch):
    captured = {}
    monkeypatch.setattr(duckduckgo.requests, "post", _fake_post(captured))

    results = duckduckgo.search("PostgreSQL 18", count=5)

    assert captured["url"] == duckduckgo.ENDPOINT
    assert captured["data"] == {"q": "PostgreSQL 18"}
    assert [r.url for r in results] == [
        "https://www.postgresql.org/docs/release/18/",
        "https://example.com/post",
    ]
    assert results[0].title == "Release Notes - PostgreSQL"
    assert results[0].snippet == "PostgreSQL 18 release notes."
    assert results[1].snippet is None


@pytest.mark.parametrize("freshness,df", [("pd", "d"), ("pw", "w"), ("pm", "m"), ("py", "y")])
def test_search_maps_freshness_to_df(monkeypatch, freshness, df):
    captured = {}
    monkeypatch.setattr(duckduckgo.requests, "post", _fake_post(captured))

    duckduckgo.search("q", freshness=freshness)
    assert captured["data"]["df"] == df


def test_search_ignores_unknown_freshness(monkeypatch):
    captured = {}
    monkeypatch.setattr(duckduckgo.requests, "post", _fake_post(captured))

    duckduckgo.search("q", freshness="bogus")
    assert "df" not in captured["data"]


def test_search_clamps_count(monkeypatch):
    monkeypatch.setattr(duckduckgo.requests, "post", _fake_post({}))
    results = duckduckgo.search("q", count=100)
    assert len(results) == 2  # 只有 2 条有效结果（第三条 href 为空）


def test_search_raises_provider_error_on_http_failure(monkeypatch):
    monkeypatch.setattr(duckduckgo.requests, "post", _fake_post({}, text="", status=500))
    with pytest.raises(ProviderError) as excinfo:
        duckduckgo.search("q")
    assert not isinstance(excinfo.value, ProviderQuotaError)


def test_search_raises_and_cools_down_on_antibot_page(monkeypatch):
    monkeypatch.setattr(
        duckduckgo.requests, "post", _fake_post({}, text="<html>anomaly</html>", status=202)
    )
    with pytest.raises(ProviderError):
        duckduckgo.search("q")
    assert duckduckgo._blocked_until > 0

    # 冷却期内不再发请求：换成会成功的桩也不该被调用
    called = False

    def _boom(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeResponse(SAMPLE_HTML)

    monkeypatch.setattr(duckduckgo.requests, "post", _boom)
    with pytest.raises(ProviderError):
        duckduckgo.search("q2")
    assert not called

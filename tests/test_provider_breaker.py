"""Tests for provider error classification and the daily circuit breaker."""
import time

import pytest
import requests

from src import config, service
from src.providers import ProviderError, ProviderQuotaError, classify_error

ALL_KEYS = {
    "BRAVE_API_KEY": "k",
    "FIRECRAWL_API_KEY": "k",
    "TAVILY_API_KEY": "k",
    "PARALLEL_API_KEY": "k",
}


@pytest.fixture(autouse=True)
def _reset_breaker():
    service._COOLDOWN_UNTIL.clear()
    yield
    service._COOLDOWN_UNTIL.clear()


def _set_keys(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setattr(config, key, value)


def _chain_names(monkeypatch, order, keys):
    _set_keys(monkeypatch, **keys)
    monkeypatch.setattr(config, "SEARCH_PROVIDER_ORDER", order)
    return [name for name, _ in service._provider_chain()]


def test_classify_quota_error_by_http_status():
    response = requests.Response()
    response.status_code = 402
    exc = requests.HTTPError("402 Client Error", response=response)
    assert isinstance(classify_error("brave", exc), ProviderQuotaError)


def test_classify_quota_error_by_message():
    assert isinstance(classify_error("tavily", RuntimeError("usage limit reached")), ProviderQuotaError)


def test_classify_transient_error_is_not_quota():
    err = classify_error("brave", RuntimeError("connection reset by peer"))
    assert isinstance(err, ProviderError)
    assert not isinstance(err, ProviderQuotaError)


def test_sequential_chain_skips_unconfigured_providers(monkeypatch):
    names = _chain_names(
        monkeypatch,
        "sequential",
        {"BRAVE_API_KEY": "k", "FIRECRAWL_API_KEY": None, "TAVILY_API_KEY": "k", "PARALLEL_API_KEY": None},
    )
    assert names == ["brave", "tavily", "duckduckgo"]


def test_sequential_chain_skips_tripped_provider(monkeypatch):
    _set_keys(monkeypatch, **ALL_KEYS)
    monkeypatch.setattr(config, "SEARCH_PROVIDER_ORDER", "sequential")
    service._trip_breaker("brave")
    assert [n for n, _ in service._provider_chain()] == ["firecrawl", "tavily", "parallel", "duckduckgo"]


def test_explicit_list_keeps_tripped_and_unconfigured_providers(monkeypatch):
    names = _chain_names(monkeypatch, "tavily,brave", {"TAVILY_API_KEY": None, "BRAVE_API_KEY": "k"})
    service._trip_breaker("tavily")
    assert names == ["tavily", "brave", "duckduckgo"]


def test_random_chain_falls_back_to_duckduckgo_when_all_tripped(monkeypatch):
    _set_keys(monkeypatch, **ALL_KEYS)
    monkeypatch.setattr(config, "SEARCH_PROVIDER_ORDER", "random")
    for name in service._PAID_PROVIDERS:
        service._trip_breaker(name)
    assert [n for n, _ in service._provider_chain()] == ["duckduckgo"]


def test_search_trips_breaker_on_quota_error(monkeypatch):
    def boom(query, count=10, freshness=None):
        raise ProviderQuotaError("brave", "402")

    monkeypatch.setattr(service, "_provider_chain", lambda: [("brave", boom)])
    with pytest.raises(service.SearchError):
        service.search("q")

    assert service._is_cooling("brave")
    assert service._COOLDOWN_UNTIL["brave"] > time.time()


def test_search_does_not_trip_breaker_on_transient_error(monkeypatch):
    def boom(query, count=10, freshness=None):
        raise ProviderError("brave", "connection reset")

    monkeypatch.setattr(service, "_provider_chain", lambda: [("brave", boom)])
    with pytest.raises(service.SearchError):
        service.search("q")

    assert not service._is_cooling("brave")


def test_breaker_recovers_at_next_local_midnight():
    service._trip_breaker("tavily")
    assert service._COOLDOWN_UNTIL["tavily"] == pytest.approx(service._next_midnight_ts(), abs=5)

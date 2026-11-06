"""Tests for the static bearer-token auth layer."""
import pytest
from fastapi.testclient import TestClient

from src import config, service
from src.auth import is_authorized
from src.main import app
from src.providers import SearchResult
from src.scrapers import ScrapeResult


@pytest.fixture(scope="module")
def client():
    # A single lifespan run: the MCP session manager can only start once.
    with TestClient(app, base_url="http://127.0.0.1:17492") as c:
        yield c


def test_is_authorized_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    assert is_authorized(None, None)


def test_is_authorized_accepts_bearer_and_api_key(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "secret")
    assert is_authorized("Bearer secret", None)
    assert is_authorized("bearer secret", None)
    assert is_authorized(None, "secret")
    assert not is_authorized(None, None)
    assert not is_authorized("Bearer wrong", None)
    assert not is_authorized("Basic secret", None)


@pytest.fixture
def fake_backends(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "secret")
    monkeypatch.setattr(
        service, "_provider_chain",
        lambda query=None: [("brave", lambda q, count=10, freshness=None: [SearchResult(title="T", url="https://e.com", snippet="s")])],
    )
    monkeypatch.setattr(
        service.jina_scraper, "scrape",
        lambda url: ScrapeResult(markdown="hello " * 10, source="jina"),
    )


def test_health_is_open(client, fake_backends):
    assert client.get("/health").status_code == 200


def test_search_requires_token(client, fake_backends):
    assert client.get("/search", params={"q": "x"}).status_code == 401
    assert client.get("/search", params={"q": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/search", params={"q": "x"}, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_scrape_accepts_x_api_key(client, fake_backends):
    assert client.post("/scrape", json={"urls": ["https://e.com"]}).status_code == 401
    ok = client.post("/scrape", json={"urls": ["https://e.com"]}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
    assert ok.json()["results"][0]["status"] == "ok"


def test_mcp_requires_token(client, fake_backends):
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401


def test_mcp_no_trailing_slash_redirect(client, fake_backends):
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        },
        headers={"Accept": "application/json, text/event-stream", "Authorization": "Bearer secret"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "location" not in {k.lower() for k in r.headers}

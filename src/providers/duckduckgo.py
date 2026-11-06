"""DuckDuckGo search provider (zero API key fallback).

直接请求 ``html.duckduckgo.com`` 的表单接口，不依赖 ``duckduckgo-search`` 包：
该包 8.1.1 已改名 ``ddgs``，并通过内嵌的 TLS 客户端去打 ``www.bing.com``，
在受限网络下会稳定失败（``peer misbehaved`` / ``return None``）。
官方 html 端点本身可直连，POST 返回干净的结果列表。

注意：短时间连续请求会触发 202 反爬页（返回空结果），只当临时失败处理，
不熔断——它本来就是低频的最后兜底。
"""
import logging
import re
import time
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

from src import config
from src.providers import ProviderError, SearchResult

logger = logging.getLogger(__name__)

ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 20

# 202 反爬页是 IP 级限流，冷却期内直接跳过，避免每次都白打一发。
_BLOCK_SECONDS = 300
_blocked_until = 0.0

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# freshness -> DuckDuckGo 的 df（date filter）参数：d / w / m / y
_FRESHNESS = {"pd": "d", "pw": "w", "pm": "m", "py": "y"}


class _ResultParser(HTMLParser):
    """收集 ``result__a``（标题+链接）与紧随其后的 ``result__snippet``。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[dict] = []
        self._kind: Optional[str] = None
        self._buf: List[str] = []
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attr = dict(attrs)
        classes = attr.get("class", "")
        if "result__a" in classes:
            self._kind = "title"
            self._buf = []
            self._href = attr.get("href", "")
        elif "result__snippet" in classes:
            self._kind = "snippet"
            self._buf = []

    def handle_data(self, data):
        if self._kind:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or not self._kind:
            return
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if self._kind == "title":
            self.items.append({"title": text, "url": self._href, "snippet": None})
        elif self.items and not self.items[-1]["snippet"]:
            self.items[-1]["snippet"] = text or None
        self._kind = None
        self._buf = []


def _unwrap_url(href: str) -> str:
    """GET 形态会包一层 //duckduckgo.com/l/?uddg=<encoded>，还原目标地址。"""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search DuckDuckGo. freshness accepts 'pd', 'pw', 'pm', 'py'."""
    global _blocked_until
    if time.time() < _blocked_until:
        raise ProviderError("duckduckgo", "anti-bot cooldown (HTTP 202 seen recently)")

    payload = {"q": query}
    df = _FRESHNESS.get((freshness or "").strip().lower())
    if df:
        payload["df"] = df

    try:
        resp = requests.post(
            ENDPOINT,
            data=payload,
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=config.SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        raise ProviderError("duckduckgo", str(exc)) from exc

    if resp.status_code == 202:
        # 连续请求触发反爬页；冷却 5 分钟，期间直接跳过，不熔断到次日。
        _blocked_until = time.time() + _BLOCK_SECONDS
        logger.warning("DuckDuckGo anti-bot page (HTTP 202), cooling down %ds", _BLOCK_SECONDS)
        raise ProviderError("duckduckgo", "anti-bot page (HTTP 202)")

    parser = _ResultParser()
    parser.feed(resp.text)

    results: List[SearchResult] = []
    for item in parser.items:
        url = _unwrap_url(item["url"])
        if not url:
            continue
        results.append(
            SearchResult(
                title=item["title"],
                url=url,
                snippet=item["snippet"],
            )
        )
        if len(results) >= min(count, MAX_RESULTS):
            break

    logger.info("DuckDuckGo returned %d results", len(results))
    return results

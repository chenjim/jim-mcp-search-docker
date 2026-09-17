"""Core search & scrape logic shared by the HTTP API and the MCP server.

Keeping this transport-agnostic lets `main.py` (FastAPI) and `mcp_server.py`
(FastMCP) reuse the exact same provider/scraper fallback chains.
"""
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime, timedelta
from typing import List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src import config
from src.providers import ProviderQuotaError, SearchResult, normalize_url
from src.providers import brave, firecrawl as firecrawl_search, tavily, parallel, duckduckgo
from src.scrapers import ScrapeResult
from src.scrapers import (
    jina as jina_scraper,
    parallel as parallel_scraper,
    firecrawl as firecrawl_scraper,
    direct as direct_scraper,
)

logger = logging.getLogger(__name__)

# Upper bounds mirroring the HTTP layer's pydantic limits, so the MCP entry
# point (which has no pydantic validation) is guarded the same way.
MAX_RESULTS = 20
MAX_SCRAPE_URLS = 20


class SearchError(Exception):
    """Raised when every search provider fails or returns nothing."""


_REGISTRY = {
    "brave": brave.search,
    "firecrawl": firecrawl_search.search,
    "tavily": tavily.search,
    "parallel": parallel.search,
    "duckduckgo": duckduckgo.search,
}

# Paid providers, in the fixed order used by the ``sequential`` mode.
_PAID_PROVIDERS = ("brave", "firecrawl", "tavily", "parallel")

# Config attribute holding each provider's API key. Providers absent from this
# map (DuckDuckGo) have no key and are always considered configured.
_PROVIDER_KEY_ATTR = {
    "brave": "BRAVE_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "parallel": "PARALLEL_API_KEY",
}

# Provider name -> timestamp it recovers after an auth/quota failure. Guarded by
# a lock because concurrent requests mutate it from worker threads.
_COOLDOWN_UNTIL: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()


def _local_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(config.TIMEZONE)
    except Exception:
        logger.warning("Unknown timezone %r, falling back to UTC", config.TIMEZONE)
        return ZoneInfo("UTC")


def _next_midnight_ts() -> float:
    """Timestamp of the next local midnight, when a tripped provider recovers."""
    now = datetime.now(_local_timezone())
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, dtime.min, tzinfo=now.tzinfo).timestamp()


def _trip_breaker(name: str) -> None:
    """Disable a provider until the next local midnight."""
    until = _next_midnight_ts()
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL[name] = until
    logger.warning(
        "Provider %s disabled until %s after quota/auth error",
        name,
        datetime.fromtimestamp(until).isoformat(timespec="seconds"),
    )


def _is_cooling(name: str) -> bool:
    with _COOLDOWN_LOCK:
        return time.time() < _COOLDOWN_UNTIL.get(name, 0.0)


def _is_available(name: str) -> bool:
    """Available = API key configured and not tripped by the daily breaker."""
    attr = _PROVIDER_KEY_ATTR.get(name)
    if attr and not getattr(config, attr, None):
        return False
    return not _is_cooling(name)


def _provider_chain():
    """Build the ordered provider chain from ``JIM_SEARCH_PROVIDER_ORDER``.

    ``random`` shuffles the *available* paid providers to spread free quota
    evenly; ``sequential`` keeps the fixed Brave → Firecrawl → Tavily → Parallel
    order. An explicit comma-separated list (e.g. ``tavily,brave``) is honoured
    as-is — those providers are tried even when unconfigured or tripped, since
    the user named them. DuckDuckGo is always appended last as the zero-cost
    fallback unless it is listed explicitly.
    """
    setting = (config.SEARCH_PROVIDER_ORDER or "random").strip().lower()
    names: List[str]
    if setting == "random":
        names = [name for name in _PAID_PROVIDERS if _is_available(name)]
        random.shuffle(names)
        logger.info("Random provider order: %s", names)
    elif setting == "sequential":
        names = [name for name in _PAID_PROVIDERS if _is_available(name)]
    else:
        names = []
        for raw in setting.split(","):
            name = raw.strip()
            if not name:
                continue
            if name not in _REGISTRY:
                logger.warning("Unknown search provider %r in JIM_SEARCH_PROVIDER_ORDER", name)
                continue
            if name not in names:
                names.append(name)
    if "duckduckgo" not in names:
        names.append("duckduckgo")
    logger.info("Provider order: %s", names)
    return [(name, _REGISTRY[name]) for name in names]


def search(query: str, top: int = 10, freshness: Optional[str] = None) -> dict:
    """Search the web and return results (title/url/snippet) without scraping."""
    top = max(1, min(int(top), MAX_RESULTS))
    logger.info("Search request: q=%s top=%d freshness=%s", query, top, freshness)

    raw_results: List[SearchResult] = []
    search_chain_used: List[str] = []
    last_error: Optional[str] = None

    for name, provider_fn in _provider_chain():
        try:
            results = provider_fn(query, count=top, freshness=freshness)
        except ProviderQuotaError as exc:
            # Auth/quota failure: stop wasting a fallback attempt per request today.
            _trip_breaker(name)
            last_error = str(exc)
            continue
        except Exception as exc:
            logger.warning("Provider %s raised exception: %s", name, exc)
            last_error = str(exc)
            continue

        if results:
            raw_results = results
            search_chain_used.append(name)
            break
        logger.info("Provider %s returned no results", name)

    if not raw_results:
        raise SearchError(
            f"All search providers failed or returned no results. Last error: {last_error}"
        )

    # Deduplicate by normalized URL and trim to top
    seen: set[str] = set()
    results_out: List[dict] = []
    for r in raw_results:
        key = normalize_url(r.url)
        if key and key not in seen:
            seen.add(key)
            results_out.append({"title": r.title, "url": r.url, "snippet": r.snippet})
        if len(results_out) >= top:
            break

    return {
        "query": query,
        "search_chain_used": search_chain_used,
        "results": results_out,
        "metadata": {"total_results": len(results_out)},
    }


def _truncate(markdown: str, max_chars: Optional[int]) -> str:
    limit = config.MAX_CHARS if max_chars is None else max(0, int(max_chars))
    if limit and limit > 0 and len(markdown) > limit:
        return markdown[:limit]
    return markdown


def _is_wechat(url: str) -> bool:
    """mp.weixin.qq.com 走专用直连通道，绕过 jina/Firecrawl 的反爬拦截。"""
    try:
        return urlparse(url).netloc == "mp.weixin.qq.com"
    except ValueError:
        return False


def _is_blocked(markdown: str) -> bool:
    """命中反爬/占位页特征词即视为抓取失败，继续 fallback。"""
    markers = ("环境异常", "完成验证", "captcha", "访问过于频繁")
    return any(marker in markdown for marker in markers)


def _finish(result: ScrapeResult, max_chars: Optional[int]) -> ScrapeResult:
    if result.markdown:
        return result.model_copy(
            update={"markdown": _truncate(result.markdown, max_chars)}
        )
    return result


def scrape_one(url: str, max_chars: Optional[int] = None) -> ScrapeResult:
    """Scrape a single URL.

    微信域名直接走 direct 通道；其他域名按 jina → Parallel → Firecrawl → direct 兜底。
    反爬/占位页内容（即便长度达标）也会被判失败继续 fallback。
    """
    if _is_wechat(url):
        return _finish(direct_scraper.scrape(url), max_chars)

    for scraper in (jina_scraper, parallel_scraper, firecrawl_scraper):
        result = scraper.scrape(url)
        if result.markdown and not _is_blocked(result.markdown):
            return _finish(result, max_chars)
        logger.info("Scraper %s skipped for %s: %s", result.source, url, result.error)

    return _finish(direct_scraper.scrape(url), max_chars)


def _safe_scrape_one(url: str, max_chars: Optional[int]) -> ScrapeResult:
    """Wrap scrape_one so a single failure can't abort the whole batch."""
    try:
        return scrape_one(url, max_chars)
    except Exception as exc:
        logger.warning("Scrape raised for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="", error=str(exc))


def scrape_urls(
    urls: List[str],
    concurrency: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> dict:
    """Scrape multiple URLs concurrently, preserving input order."""
    urls = list(urls)[:MAX_SCRAPE_URLS]
    if not urls:
        return {
            "scrape_chain_used": [],
            "results": [],
            "metadata": {"scraped_count": 0, "failed_count": 0},
        }

    workers = concurrency or config.SCRAPE_CONCURRENCY
    workers = max(1, min(workers, len(urls), MAX_SCRAPE_URLS))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda u: _safe_scrape_one(u, max_chars), urls))

    scrape_chain_used: List[str] = []
    out: List[dict] = []
    scraped_count = 0
    failed_count = 0

    for url, result in zip(urls, results):
        ok = bool(result.markdown)
        if ok:
            scraped_count += 1
            if result.source and result.source not in scrape_chain_used:
                scrape_chain_used.append(result.source)
        else:
            failed_count += 1

        out.append(
            {
                "url": url,
                "markdown": result.markdown,
                "scrape_source": result.source if ok else None,
                "status": "ok" if ok else "failed",
                "error": None if ok else result.error,
            }
        )

    return {
        "scrape_chain_used": scrape_chain_used,
        "results": out,
        "metadata": {"scraped_count": scraped_count, "failed_count": failed_count},
    }

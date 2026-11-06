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

from src import config, cookies, recorder, routing
from src.providers import ProviderQuotaError, SearchResult, normalize_url
from src.providers import (
    brave,
    firecrawl as firecrawl_search,
    tavily,
    parallel,
    duckduckgo,
    baidu,
    deepseek,
)
from src.scrapers import ScrapeResult
from src.scrapers import (
    jina as jina_scraper,
    deepseek as deepseek_scraper,
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
    "baidu": baidu.search,
    "deepseek": deepseek.search,
    "duckduckgo": duckduckgo.search,
}

# Paid providers, in the fixed order used by the ``sequential`` mode.
# 兜底源（deepseek）不在这里：它只在主链全部失败后才被调用，见 FALLBACK_PROVIDERS。
_PAID_PROVIDERS = ("brave", "firecrawl", "tavily", "parallel", "baidu")

# Config attribute holding each provider's API key. Providers absent from this
# map (DuckDuckGo) have no key and are always considered configured.
_PROVIDER_KEY_ATTR = {
    "brave": "BRAVE_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "baidu": "BAIDU_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
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


def _fallback_names(names: List[str]) -> List[str]:
    """``JIM_SEARCH_FALLBACK_PROVIDERS`` 中可用、且不在主链里的源。"""
    extra: List[str] = []
    for name in config.FALLBACK_PROVIDERS:
        if not name or name in names or name in extra:
            continue
        if name not in _REGISTRY:
            logger.warning("Unknown fallback provider %r in JIM_SEARCH_FALLBACK_PROVIDERS", name)
            continue
        if not _is_available(name):
            logger.info("Fallback provider %s skipped (no key or tripped)", name)
            continue
        extra.append(name)
    return extra


def _configured_chain() -> List[str]:
    """``JIM_SEARCH_PROVIDER_ORDER`` 展开出的基础顺序（路由关闭时的完整链）。"""
    setting = (config.SEARCH_PROVIDER_ORDER or "random").strip().lower()
    names: List[str]
    shuffled = False
    if setting.startswith("shuffle:"):
        setting = setting.split(":", 1)[1].strip()
        shuffled = True
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
        if shuffled and names:
            random.shuffle(names)
    return names


def _provider_chain(query: Optional[str] = None):
    """Build the ordered provider chain.

    开启 ``JIM_SEARCH_ROUTING``（默认）且给了 query 时，链首由
    ``routing.classify`` 按类型决定（中文政策→DeepSeek、英文文档→Parallel 等），
    再按 ``JIM_SEARCH_PROVIDER_ORDER`` 的顺序补齐剩余源；关掉路由则完全沿用
    下面的基础顺序。

    ``random`` shuffles the *available* paid providers to spread free quota
    evenly; ``sequential`` keeps the fixed Brave → Firecrawl → Tavily → Parallel
    order. An explicit comma-separated list (e.g. ``tavily,brave``) is honoured
    as-is — those providers are tried even when unconfigured or tripped, since
    the user named them. A ``shuffle:`` prefixed list (e.g.
    ``shuffle:brave,tavily,parallel,baidu``) restricts membership to the named
    providers but randomises their order per request, spreading free quota
    evenly across exactly that set.

    ``JIM_SEARCH_FALLBACK_PROVIDERS`` (default empty; e.g. ``deepseek``) is
    appended after the main chain and before DuckDuckGo, so a cheap backstop is
    only billed when the primary sources fail or return nothing. Fallbacks are
    skipped when unconfigured or tripped.

    DuckDuckGo is always appended last as the zero-cost fallback unless it is
    listed explicitly.
    """
    base = _configured_chain()
    route = None
    if config.ROUTING_ENABLED and query:
        route = routing.classify(query)
        routed = [name for name in routing.providers_for(query) if _is_available(name)]
        names = routed + [name for name in base if name not in routed]
    else:
        names = list(base)

    names.extend(name for name in _fallback_names(names) if name not in names)
    if "duckduckgo" not in names:
        names.append("duckduckgo")
    logger.info("Provider order (%s): %s", route or "no-route", names)
    return [(name, _REGISTRY[name]) for name in names]


def search(query: str, top: int = 10, freshness: Optional[str] = None) -> dict:
    """Search the web and return results (title/url/snippet) without scraping."""
    top = max(1, min(int(top), MAX_RESULTS))
    logger.info("Search request: q=%s top=%d freshness=%s", query, top, freshness)

    chain = _provider_chain(query)
    route = routing.classify(query) if config.ROUTING_ENABLED else None
    started = time.monotonic()
    raw_results: List[SearchResult] = []
    search_chain_used: List[str] = []
    attempts: List[dict] = []
    last_error: Optional[str] = None

    for name, provider_fn in chain:
        attempt_started = time.monotonic()
        try:
            results = provider_fn(query, count=top, freshness=freshness)
        except ProviderQuotaError as exc:
            # Auth/quota failure: stop wasting a fallback attempt per request today.
            _trip_breaker(name)
            last_error = str(exc)
            attempts.append(
                {
                    "provider": name,
                    "ms": int((time.monotonic() - attempt_started) * 1000),
                    "count": 0,
                    "error": str(exc),
                    "quota": True,
                }
            )
            continue
        except Exception as exc:
            logger.warning("Provider %s raised exception: %s", name, exc)
            last_error = str(exc)
            attempts.append(
                {
                    "provider": name,
                    "ms": int((time.monotonic() - attempt_started) * 1000),
                    "count": 0,
                    "error": str(exc),
                }
            )
            continue

        attempts.append(
            {
                "provider": name,
                "ms": int((time.monotonic() - attempt_started) * 1000),
                "count": len(results),
            }
        )
        if results:
            raw_results = results
            search_chain_used.append(name)
            break
        logger.info("Provider %s returned no results", name)

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

    entry = {
        "event": "search",
        "query": query,
        "top": top,
        "freshness": freshness,
        "route": route,
        "chain_planned": [name for name, _ in chain],
        "attempts": attempts,
        "chain_used": search_chain_used,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "result_count": len(results_out),
        "results": [
            {"rank": i + 1, "title": r["title"], "url": r["url"], "snippet": r["snippet"]}
            for i, r in enumerate(results_out)
        ],
    }
    if not results_out:
        entry["error"] = last_error or "All search providers returned no results"
    recorder.record("search", entry)

    if not raw_results:
        raise SearchError(
            f"All search providers failed or returned no results. Last error: {last_error}"
        )

    return {
        "query": query,
        "search_chain_used": search_chain_used,
        "results": results_out,
        "metadata": {"total_results": len(results_out), "route": route},
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
    """命中反爬/占位页特征词即视为抓取失败，继续 fallback。

    判定与 direct 通道共用：反爬页都是短壳页，长页面里的 captcha 字样
    通常只是站内 JS 变量名（实测今日头条正常页面也含），不能据此判失败。
    """
    return direct_scraper.looks_blocked(markdown)


def _finish(result: ScrapeResult, max_chars: Optional[int]) -> ScrapeResult:
    if result.markdown:
        return result.model_copy(
            update={"markdown": _truncate(result.markdown, max_chars)}
        )
    return result


def scrape_one(url: str, max_chars: Optional[int] = None) -> ScrapeResult:
    """Scrape a single URL.

    通道选择：
    - 微信域名 → direct 专用通道（jina/Firecrawl 都拿不到微信正文）
    - 配了 cookie 的域名 → 优先带 cookie 直连（快且省 jina/付费额度），失败再走常规链路
    - JINA_ONLY_DOMAINS（默认 zhihu.com）→ 跳过 direct，直连注定过不了 JS 挑战
    - 其余 → jina → DeepSeek Fetch → Parallel → Firecrawl → direct 兜底

    反爬/占位页内容（短壳页命中特征词）会被判失败并继续 fallback。
    """
    if _is_wechat(url):
        return _finish(direct_scraper.scrape(url), max_chars)

    jina_only = cookies.matches_domain(url, config.JINA_ONLY_DOMAINS)
    cookie_name = cookies.cookie_name_for(url, config.COOKIE_MAP)
    cookie = cookies.load_cookie(cookie_name) if cookie_name else ""
    direct_tried = False

    if cookie and config.COOKIE_FIRST:
        direct_tried = True
        result = direct_scraper.scrape(url, cookie=cookie, referer=cookies.referer_for(url))
        if result.markdown and not _is_blocked(result.markdown):
            return _finish(result, max_chars)
        logger.info("Cookie direct scrape missed for %s: %s", url, result.error)

    for scraper in (jina_scraper, deepseek_scraper, parallel_scraper, firecrawl_scraper):
        result = scraper.scrape(url)
        if result.markdown and not _is_blocked(result.markdown):
            return _finish(result, max_chars)
        logger.info("Scraper %s skipped for %s: %s", result.source, url, result.error)

    if jina_only or direct_tried:
        reason = "jina-only domain, direct skipped" if jina_only else "cookie direct already tried"
        return ScrapeResult(markdown=None, source="", error=f"All channels failed ({reason})")
    return _finish(direct_scraper.scrape(url), max_chars)


def _safe_scrape_one(url: str, max_chars: Optional[int]) -> ScrapeResult:
    """Wrap scrape_one so a single failure can't abort the whole batch."""
    started = time.monotonic()
    try:
        result = scrape_one(url, max_chars)
    except Exception as exc:
        logger.warning("Scrape raised for %s: %s", url, exc)
        result = ScrapeResult(markdown=None, source="", error=str(exc))

    recorder.record(
        "scrape",
        {
            "event": "scrape",
            "url": url,
            "status": "ok" if result.markdown else "failed",
            "source": result.source or None,
            "chars": len(result.markdown or ""),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": result.error,
        },
    )
    return result


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

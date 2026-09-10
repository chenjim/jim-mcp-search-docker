"""Core search & scrape logic shared by the HTTP API and the MCP server.

Keeping this transport-agnostic lets `main.py` (FastAPI) and `mcp_server.py`
(FastMCP) reuse the exact same provider/scraper fallback chains.
"""
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from src import config
from src.providers import SearchResult, normalize_url
from src.providers import brave, firecrawl as firecrawl_search, tavily, duckduckgo
from src.scrapers import ScrapeResult
from src.scrapers import jina as jina_scraper, firecrawl as firecrawl_scraper

logger = logging.getLogger(__name__)

# Upper bounds mirroring the HTTP layer's pydantic limits, so the MCP entry
# point (which has no pydantic validation) is guarded the same way.
MAX_RESULTS = 20
MAX_SCRAPE_URLS = 20


class SearchError(Exception):
    """Raised when every search provider fails or returns nothing."""


def _provider_chain():
    """Build the ordered provider chain.

    random mode shuffles the paid providers to spread free quota evenly;
    DuckDuckGo always stays last as the zero-cost fallback.
    """
    pool = [
        ("brave", brave.search),
        ("firecrawl", firecrawl_search.search),
        ("tavily", tavily.search),
    ]
    if config.SEARCH_PROVIDER_ORDER == "random":
        random.shuffle(pool)
        logger.info("Random provider order: %s", [n for n, _ in pool])
    return pool + [("duckduckgo", duckduckgo.search)]


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


def scrape_one(url: str, max_chars: Optional[int] = None) -> ScrapeResult:
    """Scrape a single URL via jina.ai, falling back to Firecrawl."""
    jina_result = jina_scraper.scrape(url)
    if jina_result.markdown:
        return jina_result.model_copy(
            update={"markdown": _truncate(jina_result.markdown, max_chars)}
        )

    fc_result = firecrawl_scraper.scrape(url)
    if fc_result.markdown:
        return fc_result.model_copy(
            update={"markdown": _truncate(fc_result.markdown, max_chars)}
        )
    # Both channels failed; surface the Firecrawl error (last attempted).
    return ScrapeResult(markdown=None, source="firecrawl", error=fc_result.error)


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

"""FastAPI HTTP server for unified web research."""
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import config
from src.providers import SearchResult, normalize_url
from src.providers import brave, firecrawl as firecrawl_search, tavily, duckduckgo
from src.scrapers import ScrapeResult
from src.scrapers import jina as jina_scraper, firecrawl as firecrawl_scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="jim-search",
    description="Unified search + scrape API: Brave → Firecrawl → Tavily → DuckDuckGo; scrape via jina.ai → Firecrawl.",
    version="0.1.0",
)


class SourceOut(BaseModel):
    title: str
    url: str
    search_source: str
    scrape_source: Optional[str] = None
    markdown: Optional[str] = None
    status: str = Field(..., pattern="^(ok|search_only|failed)$")


class SearchResponse(BaseModel):
    query: str
    search_chain_used: List[str]
    scrape_chain_used: List[str]
    sources: List[SourceOut]
    metadata: dict


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., description="Search query"),
    top: int = Query(10, ge=1, le=20, description="Number of search results to return"),
    scrape: int = Query(5, ge=0, le=20, description="Number of top results to scrape full content"),
    freshness: Optional[str] = Query(None, description="Time filter: pd, pw, pm, py or date range"),
):
    """Unified search endpoint with provider fallback and scrape fallback."""
    logger.info("Search request: q=%s top=%d scrape=%d freshness=%s", q, top, scrape, freshness)

    # Provider fallback chain
    providers = [
        ("brave", brave.search),
        ("firecrawl", firecrawl_search.search),
        ("tavily", tavily.search),
        ("duckduckgo", duckduckgo.search),
    ]

    raw_results: List[SearchResult] = []
    search_chain_used: List[str] = []
    last_error: Optional[str] = None

    for name, provider_fn in providers:
        try:
            results = provider_fn(q, count=top, freshness=freshness)
        except Exception as exc:
            logger.warning("Provider %s raised exception: %s", name, exc)
            last_error = str(exc)
            continue

        if results:
            raw_results = results
            search_chain_used.append(name)
            break
        else:
            logger.info("Provider %s returned no results", name)

    if not raw_results:
        raise HTTPException(
            status_code=503,
            detail=f"All search providers failed or returned no results. Last error: {last_error}",
        )

    # Deduplicate by normalized URL and trim to top
    seen: set[str] = set()
    deduped: List[SearchResult] = []
    for r in raw_results:
        key = normalize_url(r.url)
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)
        if len(deduped) >= top:
            break

    # Scrape fallback chain
    scrape_chain_used: List[str] = []
    sources: List[SourceOut] = []
    scraped_count = 0
    failed_count = 0

    for idx, item in enumerate(deduped):
        source = SourceOut(
            title=item.title,
            url=item.url,
            search_source=search_chain_used[0],
            status="search_only",
        )

        if scrape > 0 and idx < scrape:
            # Try jina first
            jina_result = jina_scraper.scrape(item.url)
            if jina_result.markdown:
                source.markdown = jina_result.markdown
                source.scrape_source = jina_result.source
                source.status = "ok"
                scraped_count += 1
                if jina_result.source not in scrape_chain_used:
                    scrape_chain_used.append(jina_result.source)
            else:
                # Fallback to Firecrawl scrape
                fc_result = firecrawl_scraper.scrape(item.url)
                if fc_result.markdown:
                    source.markdown = fc_result.markdown
                    source.scrape_source = fc_result.source
                    source.status = "ok"
                    scraped_count += 1
                    if fc_result.source not in scrape_chain_used:
                        scrape_chain_used.append(fc_result.source)
                else:
                    source.status = "failed"
                    failed_count += 1
                    if "firecrawl" not in scrape_chain_used:
                        scrape_chain_used.append("firecrawl")
        else:
            # Not scheduled for scraping; keep title/url/snippet
            pass

        sources.append(source)

    response = SearchResponse(
        query=q,
        search_chain_used=search_chain_used,
        scrape_chain_used=scrape_chain_used,
        sources=sources,
        metadata={
            "total_search_results": len(deduped),
            "scraped_count": scraped_count,
            "failed_scrape_count": failed_count,
        },
    )
    return response


@app.exception_handler(Exception)
def generic_exception_handler(_, exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})

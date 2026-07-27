"""Tavily search provider."""
import logging
from typing import List, Optional

from tavily import TavilyClient

from src import config
from src.providers import SearchResult

logger = logging.getLogger(__name__)


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search using Tavily."""
    if not config.TAVILY_API_KEY:
        logger.info("TAVILY_API_KEY not configured, skipping Tavily search")
        return []

    client = TavilyClient(api_key=config.TAVILY_API_KEY)

    FRESHNESS_MAP = {"pd": "day", "pw": "week", "pm": "month", "py": "year"}
    time_range = FRESHNESS_MAP.get(freshness) if freshness else None

    try:
        response = client.search(
            query=query,
            max_results=min(count, 20),
            time_range=time_range,
            include_answer=False,
            include_raw_content=False,
        )
    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)
        return []

    results: List[SearchResult] = []
    for item in response.get("results", []):
        results.append(
            SearchResult(
                title=item.get("title", "").strip(),
                url=item.get("url", "").strip(),
                snippet=item.get("content", "").strip()[:500] or None,
            )
        )
    logger.info("Tavily returned %d results", len(results))
    return results

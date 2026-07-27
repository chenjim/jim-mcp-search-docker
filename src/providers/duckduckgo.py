"""DuckDuckGo search provider (zero API key fallback)."""
import logging
from typing import List, Optional

from duckduckgo_search import DDGS

from src.providers import SearchResult

logger = logging.getLogger(__name__)


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search DuckDuckGo. freshness is ignored (DDGS text search does not support it)."""
    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=min(count, 20))
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []

    results: List[SearchResult] = []
    for item in raw:
        results.append(
            SearchResult(
                title=item.get("title", "").strip(),
                url=item.get("href", "").strip(),
                snippet=item.get("body", "").strip() or None,
            )
        )
    logger.info("DuckDuckGo returned %d results", len(results))
    return results

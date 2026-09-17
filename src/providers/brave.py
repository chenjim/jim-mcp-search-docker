"""Brave Search API provider."""
import logging
from typing import List, Optional

import requests

from src import config
from src.providers import SearchResult, classify_error

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search Brave. freshness accepts 'pd', 'pw', 'pm', 'py' or date range."""
    if not config.BRAVE_API_KEY:
        logger.info("BRAVE_API_KEY not configured, skipping Brave search")
        return []

    params = {
        "q": query,
        "count": min(count, 20),
        "offset": 0,
        "text_decorations": False,
        "text_format": "PlainText",
    }
    if freshness:
        params["freshness"] = freshness

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": config.BRAVE_API_KEY,
    }

    try:
        resp = requests.get(
            ENDPOINT,
            headers=headers,
            params=params,
            proxies=config.PROXIES,
            timeout=config.SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Brave search failed: %s", exc)
        raise classify_error("brave", exc) from exc

    results: List[SearchResult] = []
    for item in data.get("web", {}).get("results", []):
        results.append(
            SearchResult(
                title=item.get("title", "").strip(),
                url=item.get("url", "").strip(),
                snippet=item.get("description", "").strip() or None,
            )
        )
    logger.info("Brave returned %d results", len(results))
    return results

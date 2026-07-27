"""Firecrawl search provider."""
import logging
from typing import List, Optional

import requests

from src import config
from src.providers import SearchResult

logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = "https://api.firecrawl.dev/v1/search"


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search using Firecrawl search API."""
    if not config.FIRECRAWL_API_KEY:
        logger.info("FIRECRAWL_API_KEY not configured, skipping Firecrawl search")
        return []

    headers = {
        "Authorization": f"Bearer {config.FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "limit": min(count, 20),
        "scrapeOptions": {"formats": ["markdown"]},
    }

    try:
        resp = requests.post(
            SEARCH_ENDPOINT,
            headers=headers,
            json=payload,
            proxies=config.PROXIES,
            timeout=config.SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Firecrawl search failed: %s", exc)
        return []

    results: List[SearchResult] = []
    if not data.get("success"):
        logger.warning("Firecrawl search returned success=false: %s", data)
        return results

    for item in data.get("data", []):
        results.append(
            SearchResult(
                title=item.get("title", "").strip(),
                url=item.get("url", "").strip(),
                snippet=item.get("markdown", "").strip()[:500] or None,
            )
        )
    logger.info("Firecrawl returned %d results", len(results))
    return results

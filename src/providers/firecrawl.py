"""Firecrawl search provider."""
import logging
from typing import List, Optional

import requests

from src import config
from src.providers import SearchResult, ProviderError, classify_error

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
        raise classify_error("firecrawl", exc) from exc

    results: List[SearchResult] = []
    if not data.get("success"):
        error = data.get("error", "Firecrawl returned success=false")
        logger.warning("Firecrawl search failed: %s", error)
        raise ProviderError("firecrawl", error)

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

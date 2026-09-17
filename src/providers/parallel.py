"""Parallel AI Search API provider (https://docs.parallel.ai)."""
import logging
from typing import List, Optional

import requests

from src import config
from src.providers import SearchResult, classify_error

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.parallel.ai/v1/search"

# V1 requires at least one non-empty query, capped at 200 chars each.
MAX_QUERY_CHARS = 200
# Parallel excerpts are pre-compressed; keep our snippet in line with other providers.
MAX_SNIPPET_CHARS = 500


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search via Parallel AI. freshness is ignored (the API has no time filter)."""
    if not config.PARALLEL_API_KEY:
        logger.info("PARALLEL_API_KEY not configured, skipping Parallel search")
        return []

    search_query = query.strip()[:MAX_QUERY_CHARS]
    payload = {
        "objective": query.strip(),
        "search_queries": [search_query],
        "mode": config.PARALLEL_SEARCH_MODE,
        "advanced_settings": {"max_results": min(count, 20)},
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": config.PARALLEL_API_KEY,
    }

    try:
        resp = requests.post(
            ENDPOINT,
            headers=headers,
            json=payload,
            proxies=config.PROXIES,
            timeout=config.SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Parallel search failed: %s", exc)
        raise classify_error("parallel", exc) from exc

    results: List[SearchResult] = []
    for item in data.get("results", []):
        excerpts = [e.strip() for e in item.get("excerpts") or [] if e and e.strip()]
        snippet = " ".join(excerpts)[:MAX_SNIPPET_CHARS] or None
        results.append(
            SearchResult(
                title=(item.get("title") or "").strip(),
                url=item.get("url", "").strip(),
                snippet=snippet,
            )
        )
    logger.info("Parallel returned %d results", len(results))
    return results

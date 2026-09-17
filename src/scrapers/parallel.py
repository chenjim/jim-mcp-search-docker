"""Parallel AI Extract API scraper (full page markdown, consumes credits)."""
import logging

import requests

from src import config
from src.scrapers import ScrapeResult

logger = logging.getLogger(__name__)

EXTRACT_ENDPOINT = "https://api.parallel.ai/v1/extract"


def scrape(url: str) -> ScrapeResult:
    """Extract full page markdown via Parallel Extract.

    ``full_content`` is off by default on the API, so it is enabled explicitly
    and capped at ``MAX_CHARS`` to avoid oversized responses.
    """
    if not config.PARALLEL_API_KEY:
        return ScrapeResult(markdown=None, source="parallel", error="PARALLEL_API_KEY not configured")

    full_content: object = True
    if config.MAX_CHARS > 0:
        full_content = {"max_chars_per_result": config.MAX_CHARS}

    headers = {
        "Content-Type": "application/json",
        "x-api-key": config.PARALLEL_API_KEY,
    }
    payload = {"urls": [url], "advanced_settings": {"full_content": full_content}}

    try:
        resp = requests.post(
            EXTRACT_ENDPOINT,
            headers=headers,
            json=payload,
            proxies=config.PROXIES,
            timeout=config.SCRAPE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Parallel extract failed for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="parallel", error=str(exc))

    results = data.get("results") or []
    if not results:
        errors = data.get("errors") or []
        reason = errors[0].get("error_type") if errors else "no result"
        logger.warning("Parallel extract returned no content for %s: %s", url, reason)
        return ScrapeResult(markdown=None, source="parallel", error=f"No result ({reason})")

    markdown = (results[0].get("full_content") or "").strip()
    if len(markdown) < config.MIN_CONTENT_LENGTH:
        return ScrapeResult(
            markdown=None,
            source="parallel",
            error=f"Content too short ({len(markdown)} chars)",
        )
    return ScrapeResult(markdown=markdown, source="parallel")

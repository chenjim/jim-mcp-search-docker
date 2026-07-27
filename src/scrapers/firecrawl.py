"""Firecrawl scrape provider (higher quality, consumes credits)."""
import logging
from typing import Optional

import requests

from src import config
from src.scrapers import ScrapeResult

logger = logging.getLogger(__name__)

SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


def scrape(url: str) -> ScrapeResult:
    """Scrape a URL via Firecrawl."""
    if not config.FIRECRAWL_API_KEY:
        return ScrapeResult(markdown=None, source="firecrawl", error="FIRECRAWL_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {config.FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
    }

    try:
        resp = requests.post(
            SCRAPE_ENDPOINT,
            headers=headers,
            json=payload,
            proxies=config.PROXIES,
            timeout=config.SCRAPE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Firecrawl scrape failed for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="firecrawl", error=str(exc))

    if not data.get("success"):
        error = data.get("error", "Firecrawl returned success=false")
        logger.warning("Firecrawl scrape failed for %s: %s", url, error)
        return ScrapeResult(markdown=None, source="firecrawl", error=error)

    markdown = data.get("data", {}).get("markdown", "").strip()
    if len(markdown) < config.MIN_CONTENT_LENGTH:
        return ScrapeResult(
            markdown=None,
            source="firecrawl",
            error=f"Content too short ({len(markdown)} chars)",
        )
    return ScrapeResult(markdown=markdown, source="firecrawl")

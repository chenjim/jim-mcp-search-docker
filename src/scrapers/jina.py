"""Jina AI reader scraper (zero-cost fallback)."""
import logging
from typing import Optional

import requests

from src import config
from src.scrapers import ScrapeResult

logger = logging.getLogger(__name__)


def scrape(url: str) -> ScrapeResult:
    """Read article content via r.jina.ai."""
    endpoint = f"https://r.jina.ai/{url}"
    try:
        resp = requests.get(
            endpoint,
            proxies=config.PROXIES,
            timeout=config.SCRAPE_TIMEOUT,
            headers={
                "Accept": "text/plain",
                "User-Agent": "jim-search/1.0",
            },
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if len(text) < config.MIN_CONTENT_LENGTH:
            return ScrapeResult(
                markdown=None,
                source="jina",
                error=f"Content too short ({len(text)} chars)",
            )
        return ScrapeResult(markdown=text, source="jina")
    except Exception as exc:
        logger.warning("Jina scrape failed for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="jina", error=str(exc))

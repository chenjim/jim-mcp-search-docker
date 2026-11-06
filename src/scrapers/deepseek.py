"""DeepSeek Fetch API scraper (https://api-docs.deepseek.com/api/fetch).

单页抓取，返回 Markdown 正文；按成功抓取计费（$0.75 / 1000 页，
失败 / 404 / 空内容不计费），账号级 QPS 25（另受目标站频率限制）。
"""
import logging

import requests

from src import config
from src.scrapers import ScrapeResult

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"


def scrape(url: str) -> ScrapeResult:
    """Fetch one page as Markdown via DeepSeek."""
    if not config.DEEPSEEK_API_KEY:
        return ScrapeResult(markdown=None, source="deepseek", error="DEEPSEEK_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            ENDPOINT,
            headers=headers,
            json={"url": url},
            timeout=config.SCRAPE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("DeepSeek fetch failed for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="deepseek", error=str(exc))

    markdown = (data.get("content") or "").strip()
    if len(markdown) < config.MIN_CONTENT_LENGTH:
        return ScrapeResult(
            markdown=None,
            source="deepseek",
            error=f"Content too short ({len(markdown)} chars)",
        )
    logger.info("DeepSeek fetched %d chars from %s", len(markdown), url)
    return ScrapeResult(markdown=markdown, source="deepseek")

"""Direct HTTP scraper (browser UA) for sites that block crawler UA/IPs.

Dedicated channel for mp.weixin.qq.com and final fallback for other domains.
Zero dependencies beyond requests + stdlib.
"""
import html as html_lib
import logging
import re

import requests

from src import config
from src.scrapers import ScrapeResult

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 命中即视为抓取到了反爬/占位页，而非真实正文
BLOCKED_MARKERS = ("环境异常", "完成验证", "captcha", "访问过于频繁")


def _extract_article(html_text: str) -> str:
    """提取正文：优先 id=js_content 容器（div 深度配对），回退全页去标签。"""
    match = re.search(r'<div[^>]*id="js_content"[^>]*>(.*)', html_text, re.S)
    if match:
        rest = match.group(1)
        depth = 1
        i = 0
        while depth > 0 and i < len(rest):
            open_pos = rest.find("<div", i)
            close_pos = rest.find("</div>", i)
            if close_pos == -1:
                break
            if open_pos != -1 and open_pos < close_pos:
                depth += 1
                i = open_pos + 4
            else:
                depth -= 1
                i = close_pos + 6
        body = rest[:i]
    else:
        body = html_text

    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html_lib.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


def scrape(url: str) -> ScrapeResult:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": BROWSER_UA},
            proxies=config.PROXIES,
            timeout=config.SCRAPE_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:
        logger.warning("Direct scrape failed for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="direct", error=str(exc))

    markdown = _extract_article(text)
    if any(marker in markdown for marker in BLOCKED_MARKERS):
        return ScrapeResult(markdown=None, source="direct", error="Blocked by anti-scraping page")
    if len(markdown) < config.MIN_CONTENT_LENGTH:
        return ScrapeResult(
            markdown=None,
            source="direct",
            error=f"Content too short ({len(markdown)} chars)",
        )
    return ScrapeResult(markdown=markdown, source="direct")
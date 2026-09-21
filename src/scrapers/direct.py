"""Direct HTTP scraper (browser UA) for sites that block crawler UA/IPs.

Three roles:
- dedicated channel for mp.weixin.qq.com (jina/Firecrawl both get the WeChat
  anti-scraping page, a plain browser UA does not)
- cookie-bearing channel for domains listed in ``JIM_SEARCH_COOKIE_MAP``
- final fallback for every other domain

Zero dependencies beyond requests + stdlib.
"""
import html as html_lib
import logging
import re
from urllib.parse import urlparse

import requests

from src import config
from src.scrapers import ScrapeResult

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 命中即视为抓取到了反爬/占位页，而非真实正文
BLOCKED_MARKERS = ("环境异常", "完成验证", "captcha", "访问过于频繁", "zse-ck")

# 反爬/占位页都是几百字节到几 KB 的壳页。长页面里出现 captcha 之类的词，
# 多半只是站内 JS 的变量名（实测今日头条正常页面也含），不能据此判失败。
BLOCKED_MAX_LEN = 3000

# 被重定向到登录/访客墙（cookie 过期时的典型表现，且常常返回 200）
AUTH_WALL_HOST_PREFIXES = ("login.", "passport.", "sso.", "auth.")
AUTH_WALL_MARKERS = ("Sina Visitor System", "请先登录", "登录后查看", "请登录后")


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


def looks_blocked(text: str) -> bool:
    """反爬/占位页判定：只有短页面命中特征词才算，避免误杀正常长页面。"""
    if not text or len(text) > BLOCKED_MAX_LEN:
        return False
    return any(marker in text for marker in BLOCKED_MARKERS)


def _is_auth_wall(final_url: str, html_text: str) -> bool:
    """Cookie 过期时站点常返回 200 + 登录页，长度还超过阈值，需单独识别。"""
    host = (urlparse(final_url).hostname or "").lower()
    if any(host.startswith(prefix) for prefix in AUTH_WALL_HOST_PREFIXES):
        return True
    head = html_text[:4000]
    return any(marker in head for marker in AUTH_WALL_MARKERS)


def scrape(url: str, cookie: str = "", referer: str = "") -> ScrapeResult:
    """Fetch ``url`` directly. ``cookie``/``referer`` are injected when given."""
    headers = {"User-Agent": BROWSER_UA}
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer

    try:
        resp = requests.get(
            url,
            headers=headers,
            proxies=config.PROXIES,
            timeout=config.SCRAPE_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:
        logger.warning("Direct scrape failed for %s: %s", url, exc)
        return ScrapeResult(markdown=None, source="direct", error=str(exc))

    if _is_auth_wall(resp.url, text):
        host = urlparse(resp.url).hostname
        logger.warning("Direct scrape hit login wall for %s -> %s", url, host)
        return ScrapeResult(
            markdown=None,
            source="direct",
            error=f"Redirected to login/visitor wall ({host}); cookie likely expired",
        )

    markdown = _extract_article(text)
    if looks_blocked(markdown):
        return ScrapeResult(markdown=None, source="direct", error="Blocked by anti-scraping page")
    if len(markdown) < config.MIN_CONTENT_LENGTH:
        return ScrapeResult(
            markdown=None,
            source="direct",
            error=f"Content too short ({len(markdown)} chars)",
        )
    return ScrapeResult(markdown=markdown, source="direct")

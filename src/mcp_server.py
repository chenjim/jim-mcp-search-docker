"""MCP (Streamable HTTP) server exposing jim-search tools.

Mounted by `main.py` at `/mcp`; registered in opencode as a remote MCP server
alongside parallel-search. Tool handlers are async and offload the blocking
provider/scraper calls to a worker thread so the event loop stays responsive.
"""
import json
import logging
from typing import List, Optional

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src import config, service

logger = logging.getLogger(__name__)

# Whitelist Host headers for the /mcp endpoint: DNS rebinding protection is on,
# so LAN access (e.g. via a private IP) needs to be allow-listed explicitly.
_transport_security = TransportSecuritySettings(
    allowed_hosts=[f"{host}:*" for host in config.ALLOWED_HOSTS]
)

mcp = FastMCP(
    "jim-search",
    instructions=(
        "Local web research tools. Use web_search to find sources (title/url/snippet), "
        "then web_scrape to fetch full page markdown for the URLs you want to read. "
        "Search and scrape are separate: scrape only what you need."
    ),
    streamable_http_path="/mcp",
    transport_security=_transport_security,
)


@mcp.tool()
async def web_search(query: str, top: int = 10, freshness: Optional[str] = None) -> str:
    """Search the web and return results (title, url, snippet) without fetching pages.

    The provider is chosen server-side from the query type (policy→DeepSeek,
    docs→Parallel, community→Baidu, review→Brave) and reported as
    ``metadata.route``; there is no provider argument.

    Args:
        query: Search keywords.
        top: Number of results to return (1-20).
        freshness: Optional time filter: pd (day), pw (week), pm (month), py (year).
    """
    try:
        data = await anyio.to_thread.run_sync(service.search, query, top, freshness)
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


@mcp.tool()
async def web_scrape(
    urls: List[str],
    max_chars: Optional[int] = None,
    concurrency: Optional[int] = None,
) -> str:
    """Fetch full page content as markdown for one or more URLs (jina.ai -> DeepSeek Fetch -> Parallel -> Firecrawl -> direct fallback).

    Args:
        urls: URLs to fetch. They are scraped concurrently.
        max_chars: Per-page truncation limit. Omit for the server default (0 = unlimited).
        concurrency: Max concurrent workers. Omit for the server default.
    """
    try:
        data = await anyio.to_thread.run_sync(service.scrape_urls, urls, concurrency, max_chars)
    except Exception as exc:
        logger.warning("web_scrape failed: %s", exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)

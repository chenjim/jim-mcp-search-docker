"""Configuration loaded from environment variables."""
import os
from typing import Optional


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


# Search provider API keys
BRAVE_API_KEY: Optional[str] = _get_env("BRAVE_API_KEY")
FIRECRAWL_API_KEY: Optional[str] = _get_env("FIRECRAWL_API_KEY")
TAVILY_API_KEY: Optional[str] = _get_env("TAVILY_API_KEY")

# Service settings
HOST: str = _get_env("JIM_SEARCH_HOST", "0.0.0.0")
PORT: int = int(_get_env("JIM_SEARCH_PORT", "17492"))

# Proxy settings for outbound requests (e.g. host proxy at 127.0.0.1:7890)
HTTP_PROXY: Optional[str] = _get_env("HTTP_PROXY")
HTTPS_PROXY: Optional[str] = _get_env("HTTPS_PROXY")
PROXIES: dict = {}
if HTTP_PROXY or HTTPS_PROXY:
    if HTTP_PROXY:
        PROXIES["http"] = HTTP_PROXY
    if HTTPS_PROXY:
        PROXIES["https"] = HTTPS_PROXY

# Provider order: random = 随机均衡免费额度; sequential = 固定 Brave→Firecrawl→Tavily
SEARCH_PROVIDER_ORDER: str = _get_env("JIM_SEARCH_PROVIDER_ORDER", "random")

# Static bearer token for /search, /scrape and /mcp. Empty = auth disabled (local use only).
AUTH_TOKEN: str = (_get_env("JIM_SEARCH_AUTH_TOKEN", "") or "").strip()

# Allowed Host header values for the /mcp endpoint (DNS rebinding protection whitelist).
# Comma-separated host names or IPs; a "*" port suffix is added by mcp_server.py.
ALLOWED_HOSTS: list = [
    h.strip() for h in _get_env("JIM_SEARCH_ALLOWED_HOSTS", "localhost,127.0.0.1,192.168.31.165").split(",")
    if h.strip()
]

# Timeouts
SEARCH_TIMEOUT: int = int(_get_env("JIM_SEARCH_TIMEOUT", "30"))
SCRAPE_TIMEOUT: int = int(_get_env("JIM_SEARCH_SCRAPE_TIMEOUT", "60"))

# Default content quality threshold (minimum characters to consider a scrape successful)
MIN_CONTENT_LENGTH: int = int(_get_env("JIM_SEARCH_MIN_CONTENT_LENGTH", "200"))

# Scrape tuning: concurrent workers and per-page truncation (0 = unlimited)
SCRAPE_CONCURRENCY: int = int(_get_env("JIM_SEARCH_SCRAPE_CONCURRENCY", "5"))
MAX_CHARS: int = int(_get_env("JIM_SEARCH_MAX_CHARS", "20000"))

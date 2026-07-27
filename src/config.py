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

# Timeouts
SEARCH_TIMEOUT: int = int(_get_env("JIM_SEARCH_TIMEOUT", "30"))
SCRAPE_TIMEOUT: int = int(_get_env("JIM_SEARCH_SCRAPE_TIMEOUT", "60"))

# Default content quality threshold (minimum characters to consider a scrape successful)
MIN_CONTENT_LENGTH: int = int(_get_env("JIM_SEARCH_MIN_CONTENT_LENGTH", "200"))

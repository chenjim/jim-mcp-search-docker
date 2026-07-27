"""Search provider interfaces."""
from typing import List, Optional
from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: Optional[str] = None


def normalize_url(url: str) -> str:
    """Trim trailing slash and fragments for deduplication."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    # Keep path/query, drop fragment; strip trailing slash from path
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))

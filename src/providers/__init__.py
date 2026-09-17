"""Search provider interfaces."""
from typing import List, Optional
from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: Optional[str] = None


class ProviderError(Exception):
    """A provider call failed; the caller falls back to the next provider."""

    def __init__(self, provider: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message


class ProviderQuotaError(ProviderError):
    """Auth/quota failure (bad or exhausted key).

    The caller trips the daily circuit breaker so this provider is skipped until
    the next local midnight instead of wasting a fallback attempt per request.
    """


_QUOTA_STATUS = {401, 402, 403, 429}
_QUOTA_MARKERS = (
    "quota",
    "credit",
    "rate limit",
    "ratelimit",
    "usage limit",
    "unauthorized",
    "insufficient",
    "402",
    "429",
    "432",
)


def classify_error(provider: str, exc: Exception) -> ProviderError:
    """Map a provider exception to ProviderQuotaError or plain ProviderError.

    Quota/auth failures trip the daily breaker; anything else (network hiccups,
    server errors) only triggers a fallback so a transient blip can't disable a
    provider for the rest of the day.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    if status in _QUOTA_STATUS or any(marker in text for marker in _QUOTA_MARKERS):
        return ProviderQuotaError(provider, str(exc))
    return ProviderError(provider, str(exc))


def normalize_url(url: str) -> str:
    """Trim trailing slash and fragments for deduplication."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    # Keep path/query, drop fragment; strip trailing slash from path
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))

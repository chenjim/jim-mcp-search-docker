"""FastAPI HTTP server for unified web research.

Search and scrape are separate endpoints. The same core logic is also exposed
as an MCP server mounted at `/mcp` (see `src/mcp_server.py`).
"""
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import config, service
from src.auth import AuthMiddleware
from src.mcp_server import mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not config.AUTH_TOKEN:
        logger.warning(
            "JIM_SEARCH_AUTH_TOKEN is not set: /search, /scrape and /mcp are OPEN. "
            "Set a token before exposing this service beyond localhost."
        )
    # FastAPI does not run nested lifespans under Mount, so enter the MCP
    # session manager here to make the /mcp sub-app functional.
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="jim-search",
    description=(
        "Unified search + scrape API: Brave/Firecrawl/Tavily/Parallel/Baidu in rotation "
        "→ DeepSeek fallback → DuckDuckGo; scrape via jina.ai → DeepSeek Fetch → Parallel "
        "→ Firecrawl → direct. MCP endpoint at /mcp."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# Protect /search, /scrape and /mcp with the shared bearer token.
app.add_middleware(AuthMiddleware)


class SearchSource(BaseModel):
    title: str
    url: str
    snippet: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    search_chain_used: List[str]
    results: List[SearchSource]
    metadata: dict


class ScrapeRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=20)
    concurrency: Optional[int] = Field(None, ge=1, le=20)
    max_chars: Optional[int] = Field(None, ge=0)


class ScrapeSource(BaseModel):
    url: str
    markdown: Optional[str] = None
    scrape_source: Optional[str] = None
    status: str = Field(..., pattern="^(ok|failed)$")
    error: Optional[str] = None


class ScrapeResponse(BaseModel):
    scrape_chain_used: List[str]
    results: List[ScrapeSource]
    metadata: dict


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., description="Search query"),
    top: int = Query(10, ge=1, le=20, description="Number of results to return"),
    freshness: Optional[str] = Query(None, description="Time filter: pd, pw, pm, py or date range"),
):
    """Search the web; returns title/url/snippet only (no page content)."""
    try:
        return service.search(q, top=top, freshness=freshness)
    except service.SearchError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/scrape", response_model=ScrapeResponse)
def scrape(req: ScrapeRequest):
    """Fetch full page content (markdown) for one or more URLs."""
    return service.scrape_urls(
        req.urls, concurrency=req.concurrency, max_chars=req.max_chars
    )


# Mounted at "/" (added last, so FastAPI routes win) so the MCP route stays
# exactly "/mcp" instead of being prefix-stripped and 307-redirected to "/mcp/".
app.mount("/", mcp.streamable_http_app())


@app.exception_handler(Exception)
def generic_exception_handler(_, exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})

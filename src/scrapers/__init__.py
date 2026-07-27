"""Scraper interfaces."""
from typing import Optional
from pydantic import BaseModel


class ScrapeResult(BaseModel):
    markdown: Optional[str] = None
    source: str = ""
    error: Optional[str] = None

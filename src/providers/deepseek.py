"""DeepSeek Search API provider (https://api-docs.deepseek.com/guides/search/).

独立的搜索接口，不经过大模型，因此不产生模型 token 费用；按**成功请求**计费
（$0.75 / 1000 次，失败或空结果不计费），账号级 QPS 100。
文档：https://api-docs.deepseek.com/api/search/
"""
import logging
from datetime import date, timedelta
from typing import List, Optional

import requests

from src import config
from src.providers import SearchResult, classify_error

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.deepseek.com/v1/tools/search"

# query 上限 128 字符（含 after:/before: 操作符），count 上限 50。
MAX_QUERY_CHARS = 128
MAX_COUNT = 50
MAX_SNIPPET_CHARS = 500

# API 没有时间过滤参数，freshness 用 query 操作符 after: 表达。
_FRESHNESS_DAYS = {"pd": 1, "pw": 7, "pm": 30, "py": 365}


def _build_query(query: str, freshness: Optional[str]) -> str:
    """拼上 after: 时间条件，并保证总长不超过 128 字符。"""
    query = query.strip()
    days = _FRESHNESS_DAYS.get((freshness or "").strip().lower())
    if not days or "after:" in query or "before:" in query:
        return query[:MAX_QUERY_CHARS]
    suffix = f" after:{(date.today() - timedelta(days=days)).isoformat()}"
    return query[: MAX_QUERY_CHARS - len(suffix)].rstrip() + suffix


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search via DeepSeek. freshness accepts 'pd', 'pw', 'pm', 'py'."""
    if not config.DEEPSEEK_API_KEY:
        logger.info("DEEPSEEK_API_KEY not configured, skipping DeepSeek search")
        return []

    payload = {
        "query": _build_query(query, freshness),
        "count": max(1, min(int(count), MAX_COUNT)),
        # 只要摘要，不取正文：正文属于 /scrape 的职责，也避免响应体积过大。
        "need_content": False,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            ENDPOINT,
            headers=headers,
            json=payload,
            # DeepSeek 是国内服务，交给应用层代理环境变量决定（同 baidu，不显式指定）
            timeout=config.SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("DeepSeek search failed: %s", exc)
        raise classify_error("deepseek", exc) from exc

    results: List[SearchResult] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        # snippet 是 100-200 字的展示摘要；缺失时回退到 600-800 字的 summary。
        snippet = (item.get("snippet") or "").strip() or (item.get("summary") or "").strip()
        results.append(
            SearchResult(
                title=(item.get("title") or "").strip(),
                url=url,
                snippet=snippet[:MAX_SNIPPET_CHARS] or None,
            )
        )
    logger.info("DeepSeek returned %d results", len(results))
    return results

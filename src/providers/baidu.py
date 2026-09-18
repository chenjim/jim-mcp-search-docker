"""Baidu Qianfan AI Search provider (/v2/ai_search/chat/completions).

百度「智能搜索生成」：不传 ``model`` 时走纯搜索模式，只返回搜索结果
（``references``），不产生大模型 token 费用；传 ``model`` 才会额外做总结。
计费：每日免费 100 次，超出 0.036 元/次（截至 2026-09）。
文档：https://cloud.baidu.com/doc/qianfan-api/s/Hmbu8m06u
"""
import logging
from typing import List, Optional

import requests

from src import config
from src.providers import ProviderQuotaError, SearchResult, classify_error

logger = logging.getLogger(__name__)

ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"

# jim-search 的 freshness 取值（pd/pw/pm/py）→ 百度 search_recency_filter 枚举。
# 百度最小粒度是 week，没有 24 小时档，所以 pd 与 pw 都映射到 week。
_RECENCY = {
    "pd": "week",
    "pw": "week",
    "pm": "month",
    "py": "year",
}

# 文档给出的认证失败错误码（{"code": 216003, "message": "Authentication error..."}）。
_AUTH_CODES = {"216003"}


def search(query: str, count: int = 10, freshness: Optional[str] = None) -> List[SearchResult]:
    """Search via Baidu Qianfan AI Search. freshness accepts 'pd', 'pw', 'pm', 'py'."""
    if not config.BAIDU_API_KEY:
        logger.info("BAIDU_API_KEY not configured, skipping Baidu search")
        return []

    # baidu_search_v2 的 web top_k 上限为 20（v1 只有 10）。
    top_k = max(1, min(int(count), 20))
    payload = {
        "messages": [{"role": "user", "content": query}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": top_k}],
        "stream": False,
    }
    if freshness:
        mapped = _RECENCY.get(freshness)
        if mapped:
            payload["search_recency_filter"] = mapped

    headers = {
        "Authorization": f"Bearer {config.BAIDU_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            ENDPOINT,
            headers=headers,
            json=payload,
            # 百度是国内服务，直连即可，不走 HTTP(S)_PROXY
            timeout=config.SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Baidu search failed: %s", exc)
        raise classify_error("baidu", exc) from exc

    # 千帆部分错误以 HTTP 200 + body 内 code 返回，需单独判。
    code = data.get("code")
    if code:
        text = f"baidu code={code} {data.get('message', '')}".strip()
        if str(code) in _AUTH_CODES or "authentication" in text.lower():
            raise ProviderQuotaError("baidu", text)
        raise classify_error("baidu", Exception(text))

    items = data.get("references") or data.get("search_results") or []
    results: List[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "web") != "web":
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        snippet = (item.get("content") or "").strip()
        results.append(
            SearchResult(
                title=(item.get("title") or "").strip(),
                url=url,
                snippet=snippet[:500] or None,
            )
        )
    logger.info("Baidu returned %d results", len(results))
    return results

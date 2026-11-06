"""Configuration loaded from environment variables."""
import json
import os
from typing import Optional


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def _csv_env(name: str, default: str) -> list:
    raw = _get_env(name)
    raw = default if raw is None else raw
    return [item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()]


# Search provider API keys
BRAVE_API_KEY: Optional[str] = _get_env("BRAVE_API_KEY")
FIRECRAWL_API_KEY: Optional[str] = _get_env("FIRECRAWL_API_KEY")
TAVILY_API_KEY: Optional[str] = _get_env("TAVILY_API_KEY")
PARALLEL_API_KEY: Optional[str] = _get_env("PARALLEL_API_KEY")
# 百度千帆「智能搜索生成」（每日免费 100 次，超出 0.036 元/次）
BAIDU_API_KEY: Optional[str] = _get_env("BAIDU_API_KEY")
# DeepSeek 独立 Search/Fetch API（$0.75/1000 次成功请求，失败/空结果不计费）
DEEPSEEK_API_KEY: Optional[str] = _get_env("DEEPSEEK_API_KEY")

# Parallel AI search mode: turbo | fast | basic | advanced (fast = ~700ms, recommended)
PARALLEL_SEARCH_MODE: str = _get_env("PARALLEL_SEARCH_MODE", "fast")

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

# Provider order: random = 随机均衡免费额度; sequential = 固定 Brave→Firecrawl→Tavily→Parallel;
# 或显式逗号列表（如 tavily,parallel）——未列出的 provider 直接退出链，DuckDuckGo 未列出时自动兜底
SEARCH_PROVIDER_ORDER: str = _get_env("JIM_SEARCH_PROVIDER_ORDER", "random")

# 兜底源（逗号分隔）：不进日常轮转，主链全部失败/空结果时才试，排在 DuckDuckGo 之前。
# 未配置 key 或已熔断的源会被跳过。
FALLBACK_PROVIDERS: list = _csv_env("JIM_SEARCH_FALLBACK_PROVIDERS", "")

# 结构化请求日志目录（JSONL，按天分文件）；留空则关闭。
# 容器内由 compose 挂载 ./logs:/app/logs。
LOG_DIR: str = (_get_env("JIM_SEARCH_LOG_DIR", "") or "").strip()
LOG_RETENTION_DAYS: int = int(_get_env("JIM_SEARCH_LOG_RETENTION_DAYS", "30"))

# Timezone for the provider circuit breaker's "next local midnight" recovery.
TIMEZONE: str = _get_env("TZ", "Asia/Shanghai")

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


# --- Cookie support ---------------------------------------------------------
def _json_dict_env(name: str, default: dict) -> dict:
    raw = (_get_env(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except ValueError:
        return default
    return parsed if isinstance(parsed, dict) else default


# Domain -> cookie file stem. {"toutiao.com": "toutiao"} reads
# cookies/cookie.toutiao.txt and applies to toutiao.com and its subdomains.
# Zhihu is deliberately absent: its anti-bot is a JS challenge, so a direct
# request fails no matter what cookie is attached (see JINA_ONLY_DOMAINS).
DEFAULT_COOKIE_MAP = {
    "toutiao.com": "toutiao",
    "baidu.com": "baidu",
    "weibo.com": "weibo",
}
COOKIE_MAP: dict = _json_dict_env("JIM_SEARCH_COOKIE_MAP", DEFAULT_COOKIE_MAP)

# Directory holding cookie.<name>.txt (mounted read-only into the container).
COOKIE_DIR: str = (_get_env("JIM_SEARCH_COOKIE_DIR", "") or "").strip()

# Domains whose anti-bot is a JS challenge. Direct HTTP can never pass, so the
# direct channel is skipped entirely and the request goes straight to jina.
JINA_ONLY_DOMAINS: list = _csv_env("JIM_SEARCH_JINA_ONLY_DOMAINS", "zhihu.com")

# Try the cookie-bearing direct channel before the jina/paid scrapers. Cheap
# and fast when the cookie is valid; falls through to the normal chain when not.
COOKIE_FIRST: bool = (_get_env("JIM_SEARCH_COOKIE_FIRST", "true") or "").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

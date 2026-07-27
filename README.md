# jim-search

统一的 Web 搜索 + 网页抓取 HTTP API 服务。

搜索源 fallback 顺序：

```
Brave → Firecrawl → Tavily → DuckDuckGo
```

全文抓取 fallback 顺序：

```
jina.ai → Firecrawl scrape
```

## 启动

```bash
cd /vol1/1000/Tools/jim-search-docker
cp .env.example .env
# 编辑 .env 填入你的 API key
docker compose up -d
```

## 调用

```bash
curl -G "http://127.0.0.1:17492/search" \
  --data-urlencode "q=OpenCode CLI agent alternatives" \
  --data-urlencode "top=10" \
  --data-urlencode "scrape=5"
```

参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `q` | 搜索关键词 | 必填 |
| `top` | 返回结果数（1-20） | 10 |
| `scrape` | 抓取全文的条数 | 5 |
| `freshness` | 时间过滤：`pd`、`pw`、`pm`、`py` | 无 |

## 环境变量

| 变量 | 说明 |
|---|---|
| `BRAVE_API_KEY` | Brave Search API key |
| `FIRECRAWL_API_KEY` | Firecrawl API key |
| `TAVILY_API_KEY` | Tavily API key |
| `HTTP_PROXY` / `HTTPS_PROXY` | 出站代理，默认 `http://127.0.0.1:7890` |

## 停止

```bash
docker compose down
```

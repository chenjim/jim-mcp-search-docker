# jim-search

统一的 Web 搜索 + 网页抓取服务，同时提供 HTTP API 与 MCP（Streamable HTTP）。

搜索源由 `JIM_SEARCH_PROVIDER_ORDER` 控制：

- `random`（默认）：Brave / Firecrawl / Tavily / Parallel / Baidu 每次随机打散，均衡免费额度
- `sequential`：固定 Brave → Firecrawl → Tavily → Parallel → Baidu
- 逗号列表（如 `tavily,brave`）：严格按列出顺序，未列出的源不参与
- `shuffle:<列表>`（如 `shuffle:brave,tavily,parallel,baidu`）：成员仅限列出的源，次序每次请求随机——
  用于「指定成员内均衡免费额度、但要把某个源（如 firecrawl）排除在搜索外」的场景

DuckDuckGo 始终最后兜底（除非在显式列表中点名）。`random` / `sequential` 会跳过**未配置 key**或**当天额度/鉴权失败（熔断至次日 00:00）**的源；显式列表与 `shuffle:` 不跳过，按点名顺序照试。

`JIM_SEARCH_FALLBACK_PROVIDERS`（默认 `deepseek`）指定**兜底源**：不进日常轮转，
主链全部失败或返回空结果时才调用，位置在 DuckDuckGo 之前。未配置 key 或当天已熔断的
兜底源自动跳过。

全文抓取 fallback 顺序：

```
jina.ai → DeepSeek Fetch → Parallel Extract → Firecrawl scrape → direct
```

（命中 cookie 的域名先走带 Cookie 的 direct；微信走 direct 专用；`JINA_ONLY_DOMAINS` 跳过 direct。）

搜索与抓取是**分离**的：`/search` 只返回标题/URL/摘要，`/scrape` 按需抓取正文。

## 请求记录与多源评测

每次 `/search` 与 `/scrape` 都会在 `./logs/`（容器内 `/app/logs`）追加一行 JSONL，
按天分文件、默认保留 30 天：

- `search-YYYYMMDD.jsonl`：`query`、`top`、`freshness`、计划链路 `chain_planned`、
  每个源的尝试（`provider` / `ms` / `count` / `error`）、最终 `chain_used`、
  `elapsed_ms`、完整结果（`rank` / `title` / `url` / `snippet`）。
- `scrape-YYYYMMDD.jsonl`：`url`、`status`、`source`、`chars`、`elapsed_ms`、`error`。

关闭：把 `JIM_SEARCH_LOG_DIR` 设为空。日志含查询词，按需清理。

想横向比较各源的实际结果（同一批 query 并发直调每个源，绕过链式短路）：

```bash
docker run --rm --network host --env-file .env -e TZ=Asia/Shanghai -v "$PWD:/app" -w /app \
  jim-search:latest python scripts/eval_providers.py --top 8
```

产出 `eval/<时间戳>/`：`matrix.md`（每 query 各源 top5 并排）、`stats.md`
（成功率 / 延迟 / URL 重合度 / 独占率 / 域名分布）、`raw.jsonl`（完整结果）。
query 集在 `scripts/eval_queries.txt`，`--dry-run` 可零成本先看计划。

## 启动

```bash
cd /vol1/1000/Tools/jim-search-docker
cp .env.example .env
# 编辑 .env 填入你的 API key
docker compose up -d
```

## HTTP API

```bash
TOKEN="<your-token>"

# 搜索（不抓正文）
curl -G "http://127.0.0.1:17492/search" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "q=OpenCode CLI agent alternatives" \
  --data-urlencode "top=10"

# 抓取正文（可一次传多个 URL，并发执行）
curl -X POST "http://127.0.0.1:17492/scrape" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com/a", "https://example.com/b"]}'
```

`GET /search` 参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `q` | 搜索关键词 | 必填 |
| `top` | 返回结果数（1-20） | 10 |
| `freshness` | 时间过滤：`pd`、`pw`、`pm`、`py` | 无 |

`POST /scrape` 参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `urls` | URL 数组（1-20） | 必填 |
| `concurrency` | 并发数（1-20） | 5 |
| `max_chars` | 单页截断长度，0 表示不限 | 20000 |

## MCP

服务在 `http://127.0.0.1:17492/mcp` 暴露 MCP（Streamable HTTP），提供两个工具：

| 工具 | 说明 |
|---|---|
| `web_search(query, top, freshness)` | 搜索，返回 title/url/snippet |
| `web_scrape(urls, max_chars, concurrency)` | 并发抓取正文 markdown |

在 opencode 中注册（`~/.config/opencode/opencode.json`）：

```json
"jim-search": {
  "type": "remote",
  "url": "http://127.0.0.1:17492/mcp",
  "enabled": true,
  "headers": {
    "Authorization": "Bearer <your-token>"
  }
}
```

## 鉴权

`/search`、`/scrape`、`/mcp` 需要 Bearer token（`/health` 始终开放）。请求头二选一：

```
Authorization: Bearer <token>
X-API-Key: <token>
```

- token 由 `JIM_SEARCH_AUTH_TOKEN` 配置；为空则关闭鉴权（**仅限本机使用**）。
- 生成 token：`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`。
- 其他机器上可直接用 skill `jim-search-skill` 里的 `scripts/jimsearch.py`（纯标准库，无需额外依赖）当客户端：读 `JIM_SEARCH_BASE_URL`（远端地址）与 `JIM_SEARCH_AUTH_TOKEN`，也可把 token 写入 `~/.config/jim-search/token`。
- 对外暴露务必置于 HTTPS 反向代理（nginx/caddy）之后，避免 token 明文传输；建议同时在反代层做访问日志与限流。

## 环境变量

| 变量 | 说明 |
|---|---|
| `BRAVE_API_KEY` | Brave Search API key |
| `FIRECRAWL_API_KEY` | Firecrawl API key |
| `TAVILY_API_KEY` | Tavily API key |
| `PARALLEL_API_KEY` | Parallel AI API key（`https://platform.parallel.ai/`），用于 Search 与 Extract 抓取 |
| `PARALLEL_SEARCH_MODE` | Parallel 搜索模式 `turbo`/`fast`/`basic`/`advanced`，默认 `fast` |
| `BAIDU_API_KEY` | 百度千帆 AI 搜索 API key（`https://console.bce.baidu.com/qianfan/`），每日免费 100 次；只取搜索结果时 provider 不传 `model` |
| `DEEPSEEK_API_KEY` | DeepSeek 独立 Search / Fetch API key（`https://platform.deepseek.com/api_keys`），$0.75/1000 次成功请求，失败与空结果不计费 |
| `JIM_SEARCH_FALLBACK_PROVIDERS` | 兜底源，逗号分隔，compose 默认 `deepseek`（代码默认空）；主链全失败后才调用，排在 DuckDuckGo 之前 |
| `JIM_SEARCH_LOG_DIR` | 结构化请求日志目录；容器内固定 `/app/logs`（宿主 `./logs`），留空关闭 |
| `JIM_SEARCH_LOG_RETENTION_DAYS` | 请求日志保留天数，默认 30 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 出站代理，默认 `http://127.0.0.1:7890` |
| `JIM_SEARCH_PROVIDER_ORDER` | `random`（默认）、`sequential`、显式逗号列表（如 `tavily,brave`）或 `shuffle:<列表>`（指定成员内随机次序） |
| `JIM_SEARCH_SCRAPE_CONCURRENCY` | 抓取并发数，默认 5 |
| `JIM_SEARCH_MAX_CHARS` | 单页截断长度，默认 20000，0 不限 |
| `JIM_SEARCH_MIN_CONTENT_LENGTH` | 判定抓取成功的最小字符数，默认 200 |
| `JIM_SEARCH_AUTH_TOKEN` | 访问 `/search` `/scrape` `/mcp` 的 Bearer token；为空则关闭鉴权 |

## 停止

```bash
docker compose down
```

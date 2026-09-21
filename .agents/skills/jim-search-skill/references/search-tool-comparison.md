# 搜索工具对比与分工

## 工具矩阵

| 维度 | web_search (Hermes 内置) | jim-search (本地 Docker) |
|------|-------------------|---------------------|
| 后端 | brave-free | 多源轮转（多家商用搜索 API + 免 key 引擎兜底，集合由服务配置决定） |
| 返回内容 | title + url + description | search: title/url/snippet；scrape: markdown 全文 |
| 全文获取 | 需二次 web_extract | web_scrape 批量并发（多通道逐级兜底） |
| 多查询并行 | 不支持 | 不支持（每次调用只发单条 query） |
| 结构化输出 | 否 | JSON（search_chain_used / scrape_source / status） |
| 额度 | Brave $5/1k + 每月赠 $5（≈1000 次/月）；**与聚合服务共用同一 key、同一额度池** | 各家额度叠加（含按月免费额度） |

## 源接入与熔断规则

搜索与抓取的各家源都由服务内部承担，key 统一放聚合服务的 `.env`（变量名见其 `.env.example`）。
新增或替换源只改配置，不需要改本文档。

未配置 key、或当天命中额度/鉴权错误的源跳过至次日 00:00（`TZ`，默认 `Asia/Shanghai`）；网络抖动
等临时错误只 fallback、不熔断。

## 额度共用的坑（以 Brave 为例）

- Hermes 的 `BRAVE_SEARCH_API_KEY` 与聚合服务 `.env` 里的同名 key 是同一个账号 → 同一额度池
- Hermes `web_search` 每次都打它（≈700 次/月）；聚合服务多源轮转下命中它的概率约 1/N
- 两边合烧每月 $5 免费额度（≈1000 次）已近上限，超出 $0.005/次
- 省额度：调整聚合服务的源集合/顺序（其 `.env` 的 `JIM_SEARCH_PROVIDER_ORDER`），或换 Hermes `web.search_backend`
- 注意：显式逗号列表不享受熔断；想熔断请用 `random` / `sequential`

## 分工结论

- **默认首选 → jim-search**：搜索与取全文的统一入口（多源轮转）；本地确定性、JSON、cron 友好
- **快查事实 → web_search**（brave-free，注意与聚合服务共用同一额度池）
- **额度紧张的付费源仅兜底**：不主动调用（如按月 1000 credits 档的抓取服务）

## 省额度补充：Hermes keyless 抓取

`web.extract_backend` 可换成 keyless 供应商（`exa` / `parallel` / `firecrawl` / `keenable`）：

```bash
hermes config set web.extract_backend keenable   # 或 parallel
```

## API 返回格式（权威）

`web_search(query, top, freshness)` / `GET /search`：

```json
{"query":"...","search_chain_used":["brave"],"results":[{"title":"","url":"","snippet":""}],"metadata":{"total_results":10}}
```

`web_scrape(urls, max_chars, concurrency)` / `POST /scrape`：

```json
{"scrape_chain_used":["jina"],"results":[{"url":"","markdown":"","scrape_source":"jina|parallel|firecrawl|direct|null","status":"ok|failed","error":null}],"metadata":{"scraped_count":5,"failed_count":0}}
```

服务 `http://192.168.31.165:17492`，MCP 在 `/mcp`；均需 `Authorization: Bearer <JIM_SEARCH_AUTH_TOKEN>`
（`/health` 免鉴权）。`search_chain_used` 为随机打散取一，不是固定顺序。

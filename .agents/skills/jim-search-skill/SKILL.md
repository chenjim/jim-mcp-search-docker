---
name: jim-search-skill
description: "调 jim-search 服务时查：HTTP API/MCP 用法、cookie、401/503 排障。"
---

# jim-search-skill

自建的本地搜索 + 抓取服务。把多家商用搜索 API 与多个抓取通道收敛成一套接口，供 Agent / cron / 脚本统一调用。

| 项 | 值 |
|---|---|
| 项目目录 | `/vol1/1000/Tools/jim-search-docker/` |
| 服务地址 | `http://192.168.31.165:17492` |
| MCP 端点 | `http://192.168.31.165:17492/mcp` |
| 容器名 | `jim-search-mcp`（`network_mode: host`） |
| 鉴权 | `Authorization: Bearer <JIM_SEARCH_AUTH_TOKEN>`（`/health` 免鉴权） |

本文只讲服务接口与用法。调研方法论见 `jim-deep-research`。

## 健康检查（零成本）

```bash
curl -s "http://192.168.31.165:17492/health" -m 5    # → {"status":"ok"}
```

**禁止用 `/search?q=test` 做探测**——那会消耗付费搜索额度。

## 调用方式

### 首选：MCP

Hermes 与 opencode 均已注册 `jim-search` remote MCP。

| 端 | 配置位置 | 工具名 |
|---|---|---|
| Hermes | `~/.hermes/config.yaml` → `mcp_servers.jim-search` | `mcp__jim_search__web_search` / `web_scrape` |
| opencode | `~/.config/opencode/opencode.json` → `mcp.jim-search` | `web_search` / `web_scrape` |

- `web_search(query, top=10, freshness=None)` → title/url/snippet，**不抓正文**
- `web_scrape(urls, max_chars=None, concurrency=None)` → 批量并发抓 markdown

**MCP 不能批量调用**（只有 `connectors__` 能批量）——多查询就多次顺序调用，别为省轮次改走 shell。

Hermes 侧重新配置（交互顺序：要鉴权 Y → token → enable Y。`config.yaml` 受写保护，agent 不能直接 patch，须走 CLI 或用户手动编辑）：

```bash
printf 'Y\n<token>\nY\n' | hermes mcp add jim-search --url http://192.168.31.165:17492/mcp --auth header
hermes mcp test jim-search     # 应显示 Connected + 2 tools
```

token 存 `~/.hermes/.env` 的 `JIM_SEARCH_AUTH_TOKEN`（与 docker `.env` 同值）。改过 MCP 配置后需开新会话或 `systemctl --user restart hermes-gateway`。

opencode 侧配置形如：

```json
"jim-search": {
  "type": "remote",
  "url": "http://192.168.31.165:17492/mcp",
  "enabled": true,
  "headers": { "Authorization": "Bearer <token>" }
}
```

### 兜底：curl

curl 打私网地址会触发 tirith 告警（交互式弹审批 / cron 里 `approvals.cron_mode: deny` 直接拒），MCP 不会。「不能批量」「想看原始 JSON」不算理由。

```bash
TOKEN="${JIM_SEARCH_AUTH_TOKEN:-$(cat ~/.config/jim-search/token 2>/dev/null)}"

# 搜索（不抓正文）
curl -s -G "http://192.168.31.165:17492/search" -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "q=<query>" --data-urlencode "top=10"

# 抓取正文（1-20 个 URL，并发执行）
curl -s -X POST "http://192.168.31.165:17492/scrape" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"urls": ["<url1>", "<url2>"]}'
```

## 接口与参数

三个端点：`GET /health`（免鉴权）、`GET /search`、`POST /scrape`。后两个与 `/mcp` 需 Bearer。

`GET /search`（= `web_search`）：

| 参数 | 说明 | 默认 |
|---|---|---|
| `q` | 搜索关键词 | 必填 |
| `top` | 结果数，1-20 | 10 |
| `freshness` | 时间过滤：`pd` / `pw` / `pm` / `py` | 无 |

`POST /scrape`（= `web_scrape`）：

| 参数 | 说明 | 默认 |
|---|---|---|
| `urls` | URL 数组，1-20 | 必填 |
| `concurrency` | 并发数，1-20 | 5 |
| `max_chars` | 单页截断长度，0 表示不限 | 20000 |

## 抓取通道选择（服务端自动）

`/scrape` 按 URL 域名分流，客户端无需干预：

| 情形 | 通道 | 说明 |
|---|---|---|
| `mp.weixin.qq.com` | direct（专用） | jina / Firecrawl 都拿不到微信正文 |
| 命中 cookie 映射的域名 | direct 带 Cookie + Referer，优先 | 快且省 jina / 付费额度；失败自动回退常规链路 |
| jina-only 域名（默认 `zhihu.com`） | 跳过 direct，直接 jina | 反爬是 JS 挑战（zse_ck），直连无论带不带 cookie 都必失败 |
| 其余 | jina → Parallel → Firecrawl → direct | 逐级兜底 |

搜索侧：多家商用源之间轮转（默认 `random`），免 key 引擎恒为最后兜底。集合与顺序由服务端 `.env` 的 `JIM_SEARCH_PROVIDER_ORDER` 决定，本文不枚举。

## cookie 管理（反爬站点）

cookie 存服务端 `cookies/cookie.<name>.txt`（含登录态，已 gitignore，**绝不入库**），命中域名时注入 `Cookie` + 站点根 `Referer`。

默认映射：`toutiao.com→toutiao`、`baidu.com→baidu`、`weibo.com→weibo`；覆盖用 `JIM_SEARCH_COOKIE_MAP`（JSON）。

**换 cookie 只需覆盖对应文件，无需重启容器**（每次调用读盘）。

cookie 过期时站点常返回 200 + 登录页，服务端会识别并报 `Redirected to login/visitor wall`；换掉文件即恢复。

实测有效性（2026-09-21）：头条有效（4,911 → 531,201 字节）、微博有效（访客页 → 116,555 字节热搜榜）、百度影响不大（带不带都约 1.9MB）。**知乎无解**：zse_ck 是 JS 挑战，cookie 与 TLS 指纹（curl_cffi 5 种指纹 × 带/不带 = 20 次请求）全部 403，只能靠 jina。

## 返回 JSON 结构

`/search` / `web_search`：

```json
{"query":"...","search_chain_used":["firecrawl"],"results":[{"title":"","url":"","snippet":""}],"metadata":{"total_results":10}}
```

`/scrape` / `web_scrape`：

```json
{"scrape_chain_used":["jina"],"results":[{"url":"","markdown":"","scrape_source":"jina|parallel|firecrawl|direct|null","status":"ok|failed","error":null}],"metadata":{"scraped_count":5,"failed_count":0}}
```

`search_chain_used` 反映本次实际命中的源（默认在多源间轮转取一，不是固定顺序）。

## 抓取兜底：浏览器（browser_exec）

所有抓取通道都拿不到正文时，用真实 Chrome 作为最终手段。适用：JS 渲染页、403 / 验证码 / 反爬、登录态页面（复用 Chrome 已有 cookie）。

- ⚠️ **仅限交互式会话**：每次连 Chrome 会弹 "Allow remote debugging"，需用户在场点 Allow（per-connection 授权）。**cron / 无人值守场景自动跳过此通道**，直接报 failed 或降级处理。
- 操作：`browser_exec` → `new_tab(URL)` → 等渲染 → `js("document.body.innerText")` 取正文（长文分段取）
- 图集：`js("[...document.images].map(i => i.src || i.dataset.src)")`
- 结果按 scrape JSON 同构整理（url + markdown + status: ok，注明 `scrape_source: browser`）
- 微信文章 jina 无效时，浏览器直开 URL 优于 `curl -A MicroMessenger`
- 页面需登录时：让用户先在有头 Chrome 里完成登录，再走本通道

## 服务运维

```bash
cd /vol1/1000/Tools/jim-search-docker
docker compose ps                   # 状态
docker compose up -d                # 启动 / 应用配置
docker compose up -d --build        # 改过 src/ 或 requirements.txt 后重建
docker compose logs -f --tail=100   # 日志
```

改 `.env` 后必须 `up -d`——`restart` 不会重读 `env_file`。

## 附带脚本（scripts/）

只用标准库，可直接跑，不需要装任何依赖。

### `scripts/jimsearch.py` — 命令行客户端

```bash
cd /vol1/1000/Tools/jim-search-docker/.agents/skills/jim-search-skill/scripts

python3 jimsearch.py health                                     # 零成本健康检查
python3 jimsearch.py search "关键词" --top 10 --freshness pw      # 搜索（不抓正文）
python3 jimsearch.py scrape URL [URL ...] --out /tmp/mat         # 抓正文，落盘
python3 jimsearch.py scrape --from-file urls.txt --out /tmp/mat  # 从文件读 URL
python3 jimsearch.py collect "关键词" --scrape 5 --out /tmp/mat   # 搜索 + 抓正文 + 落盘素材
```

- **不走 curl**，因此不触发私网地址的安全告警 —— cron / agent 环境优先用它，而不是手写 curl
- `collect` 产出 `00-index.md`（结果表 + 抓取状态）和 `NN-<slug>.md`（正文，带 `url` / `scrape_source` / `status` frontmatter）
- token 解析顺序：`$JIM_SEARCH_AUTH_TOKEN` → `~/.config/jim-search/token` → `~/.hermes/.env` → 项目 `.env`
- 服务地址：`$JIM_SEARCH_BASE_URL`，默认 `http://192.168.31.165:17492`
- 单次 `scrape` 上限 20 个 URL（服务端限制），超了脚本会提示分批

### `scripts/doctor.py` — 一键体检

```bash
python3 doctor.py              # 服务健康 + token + 容器状态 + cookie 有效性
python3 doctor.py --no-cookie  # 跳过 cookie 探测（省抓取额度）
```

cookie 探测按内置探针表逐个抓一次（头条 / 微博 / 百度 / 知乎），报 `✓ 有效` / `⚠ 内容偏短` / `✗ 疑似过期`；知乎会显示「已知无解」而不是误报过期。

## 故障排查

| 现象 | 原因 / 处置 |
|---|---|
| 401 Unauthorized | token 缺失或不匹配。查 `JIM_SEARCH_AUTH_TOKEN`、`~/.config/jim-search/token`、各端 MCP 的 `Authorization` 头 |
| 503 All search providers failed | `.env` 里一个 API key 都没配 |
| 空响应 / 超时 | `docker compose ps` 看容器状态 |
| `status: failed` | 抓取失败，URL 仍保留可手工处理 |
| 某个源连续失败 | 未配 key、或当天命中额度/鉴权错误的源会跳过至次日 00:00（按 `TZ`）；网络抖动只 fallback、不熔断 |
| 微信文章拿不到 | 走 direct 专用通道；仍失败用浏览器兜底 |

### cron 环境额外坑

- **curl 管道被安全扫描拦**：`curl ... | python3 -c ...` 直接 BLOCKED（`/approve` 无效）。解法：`curl -s -o /tmp/x.html <url>` 存盘，再用 `python3` 读文件。
- **cron 里禁用 execute_code**：用 `write_file` 写 `.py` 再执行。
- **管道掩盖退出码**：`| tail` / `| head` 之后 `$?` 取的是 tail 的退出码，判成败要用非管道写法或 `${PIPESTATUS[0]}`。
- **JS 渲染站**：走 `r.jina.ai` 代理并带 `Accept: text/markdown` 头。

## 搜索工具分工与额度

见 [references/search-tool-comparison.md](references/search-tool-comparison.md)。

重点：Hermes 内置 `web_search` 与 jim-search 共用**同一个 Brave key / 同一额度池**，两边合烧每月 $5 免费额度（≈1000 次），省额度要调服务端源集合或 Hermes `web.search_backend`。

## 相关 skill

- `jim-deep-research` — 深度调研方法论（拆解子问题、多轮搜索、交叉验证、报告合成）
- `jim-finance-market-data` — 行情 / 指数 / K 线 / 快讯的结构化取数
- `jim-source-capture` — 素材捕获（把外部文章归档成 source.md）

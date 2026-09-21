#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jim-search 命令行客户端 —— 只用标准库，可直接运行。

    python3 jimsearch.py health
    python3 jimsearch.py search "关键词" --top 10 --freshness pw
    python3 jimsearch.py scrape https://a.com https://b.com --out /tmp/mat
    python3 jimsearch.py scrape --from-file urls.txt --out /tmp/mat
    python3 jimsearch.py collect "关键词" --top 10 --scrape 5 --out /tmp/mat

token 解析顺序：$JIM_SEARCH_AUTH_TOKEN → ~/.config/jim-search/token
                → ~/.hermes/.env → /vol1/1000/Tools/jim-search-docker/.env
服务地址：$JIM_SEARCH_BASE_URL，默认 http://192.168.31.165:17492
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://192.168.31.165:17492"
MAX_URLS = 20
HOME = pathlib.Path.home()
TOKEN_SOURCES = (
    HOME / ".config" / "jim-search" / "token",
    HOME / ".hermes" / ".env",
    pathlib.Path("/vol1/1000/Tools/jim-search-docker/.env"),
)


# ---------------------------------------------------------------- 基础设施

def load_token() -> str:
    """按顺序找 token；找不到返回空串（/health 不需要）。"""
    tok = os.environ.get("JIM_SEARCH_AUTH_TOKEN", "").strip()
    if tok:
        return tok
    for path in TOKEN_SOURCES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("JIM_SEARCH_AUTH_TOKEN="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value:
                    return value
        if path.name == "token":          # 裸 token 文件
            value = text.strip()
            if value and "=" not in value:
                return value
    return ""


def base_url() -> str:
    return os.environ.get("JIM_SEARCH_BASE_URL", DEFAULT_BASE).rstrip("/")


def api(method: str, path: str, *, params=None, body=None, timeout: int = 60) -> dict:
    url = base_url() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method)
    token = load_token()
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        sys.exit(f"[jim-search] HTTP {exc.code}: {detail}\n"
                 f"            401 多半是 token 不对；503 是服务端一个源都没配。")
    except urllib.error.URLError as exc:
        sys.exit(f"[jim-search] 连不上 {base_url()}（{exc.reason}）\n"
                 f"            起服务：cd /vol1/1000/Tools/jim-search-docker && docker compose up -d")


def slugify(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    raw = f"{parts.netloc}{parts.path}".strip("/") or "page"
    raw = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", raw)
    return raw.strip("-.")[:80] or "page"


def dump(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  → {path}  ({len(text)} 字符)")


def save_results(outdir: pathlib.Path, items: list[dict]) -> None:
    """把 scrape 结果落盘成 NN-<slug>.md，带 frontmatter。"""
    for idx, item in enumerate(items, 1):
        url = item.get("url", "")
        status = item.get("status", "?")
        source = item.get("scrape_source") or "-"
        body = item.get("markdown") or ""
        head = (f"---\nurl: {url}\nscrape_source: {source}\n"
                f"status: {status}\nerror: {item.get('error')}\n---\n\n")
        if not body and item.get("error"):
            body = f"（抓取失败：{item['error']}）\n"
        dump(outdir / f"{idx:02d}-{slugify(url)}.md", head + body)


# ---------------------------------------------------------------- 子命令

def cmd_health(args: argparse.Namespace) -> None:
    print(json.dumps(api("GET", "/health", timeout=5), ensure_ascii=False))


def cmd_search(args: argparse.Namespace) -> None:
    params = {"q": args.query, "top": str(args.top)}
    if args.freshness:
        params["freshness"] = args.freshness
    data = api("GET", "/search", params=params)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    chain = ",".join(data.get("search_chain_used") or []) or "-"
    total = data.get("metadata", {}).get("total_results", 0)
    print(f"源: {chain}   |   结果: {total}\n")
    for idx, item in enumerate(data.get("results", []), 1):
        print(f"{idx}. {item.get('title', '')}")
        print(f"   {item.get('url', '')}")
        snippet = (item.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            print(f"   {snippet[:200]}")
        print()

    if args.out:
        dump(pathlib.Path(args.out) / "search.json", json.dumps(data, ensure_ascii=False, indent=2))


def _resolve_urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.urls or [])
    if args.from_file:
        text = pathlib.Path(args.from_file).read_text(encoding="utf-8")
        urls += [ln.strip() for ln in text.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
    seen, unique = set(), []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def cmd_scrape(args: argparse.Namespace) -> None:
    urls = _resolve_urls(args)
    if not urls:
        sys.exit("没有 URL。给位置参数或用 --from-file。")
    if len(urls) > MAX_URLS:
        sys.exit(f"一次最多 {MAX_URLS} 个 URL，收到 {len(urls)}。请分批。")

    body: dict = {"urls": urls}
    if args.max_chars is not None:
        body["max_chars"] = args.max_chars
    if args.concurrency is not None:
        body["concurrency"] = args.concurrency

    data = api("POST", "/scrape", body=body, timeout=300)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        chain = ",".join(data.get("scrape_chain_used") or []) or "-"
        meta = data.get("metadata", {})
        print(f"通道: {chain}   |   成功 {meta.get('scraped_count', 0)} / 失败 {meta.get('failed_count', 0)}\n")
        for item in data.get("results", []):
            mark = "✓" if item.get("status") == "ok" else "✗"
            print(f"  {mark} [{item.get('scrape_source') or '-'}] "
                  f"{len(item.get('markdown') or '')} 字符  {item.get('url')}")
            if item.get("error"):
                print(f"      {item['error']}")

    if args.out:
        save_results(pathlib.Path(args.out), data.get("results", []))


def cmd_collect(args: argparse.Namespace) -> None:
    """搜索 → 挑前 N 条 → 抓正文 → 落盘成调研素材。"""
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    params = {"q": args.query, "top": str(args.top)}
    if args.freshness:
        params["freshness"] = args.freshness
    found = api("GET", "/search", params=params)
    results = found.get("results", [])
    if not results:
        sys.exit("搜索无结果。")

    chain = ",".join(found.get("search_chain_used") or []) or "-"
    print(f"搜索 [{chain}] {len(results)} 条，取前 {args.scrape} 条抓正文\n")

    targets = [item["url"] for item in results[: args.scrape] if item.get("url")]
    scraped = api("POST", "/scrape", body={"urls": targets}, timeout=300) if targets else {"results": []}

    by_url = {item.get("url"): item for item in scraped.get("results", [])}
    lines = [
        f"# 调研素材：{args.query}",
        "",
        f"- 采集时间：{args.stamp}",
        f"- 搜索源：{chain}",
        f"- 结果数：{len(results)}，抓正文：{len(targets)}",
        "",
        "## 搜索结果",
        "",
    ]
    for idx, item in enumerate(results, 1):
        got = by_url.get(item.get("url"), {})
        status = got.get("status") or "未抓取"
        src = got.get("scrape_source") or "-"
        lines.append(f"{idx}. [{item.get('title', '')}]({item.get('url', '')})")
        snippet = (item.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            lines.append(f"   - {snippet[:300]}")
        lines.append(f"   - 抓取：{status}（{src}）")
    lines.append("")
    dump(outdir / "00-index.md", "\n".join(lines))

    if targets:
        save_results(outdir, [by_url.get(u, {"url": u, "status": "failed",
                                            "error": "服务未返回该 URL 的结果"}) for u in targets])
    print(f"\n素材目录：{outdir}")


# ---------------------------------------------------------------- 入口

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jimsearch.py",
        description="jim-search 命令行客户端（本地搜索 + 抓取服务）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="零成本健康检查（不消耗额度）").set_defaults(func=cmd_health)

    p = sub.add_parser("search", help="搜索，只拿标题/URL/摘要，不抓正文")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=10, help="结果数 1-20（默认 10）")
    p.add_argument("--freshness", choices=["pd", "pw", "pm", "py"], help="时间过滤")
    p.add_argument("--json", action="store_true", help="输出原始 JSON")
    p.add_argument("--out", help="把 JSON 存到这个目录")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("scrape", help="抓正文（markdown）")
    p.add_argument("urls", nargs="*")
    p.add_argument("--from-file", help="从文件读 URL（每行一个，# 开头忽略）")
    p.add_argument("--max-chars", type=int, help="单页截断长度，0 不限")
    p.add_argument("--concurrency", type=int, help="并发数 1-20（默认 5）")
    p.add_argument("--json", action="store_true", help="输出原始 JSON")
    p.add_argument("--out", help="把每篇正文落盘为 NN-<slug>.md")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("collect", help="搜索 + 抓正文 + 落盘成调研素材（一站式）")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--scrape", type=int, default=5, help="抓前几条的正文（默认 5，上限 20）")
    p.add_argument("--freshness", choices=["pd", "pw", "pm", "py"])
    p.add_argument("--out", default="./jimsearch-out", help="素材目录（默认 ./jimsearch-out）")
    p.set_defaults(func=cmd_collect, stamp="")

    args = parser.parse_args()
    if args.cmd == "collect" and not args.stamp:
        from datetime import datetime
        args.stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    args.func(args)


if __name__ == "__main__":
    main()

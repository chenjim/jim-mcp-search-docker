#!/usr/bin/env python3
"""多源搜索结果对比：同一批 query 并发直调每个 provider，落原始结果与统计。

与 `/search` 的区别：不走链式短路，每个 query 都会真正问到每个源，
所以能横向比较"谁给的结果更对、更快、更独立"。

产出（默认 ``eval/<YYYYMMDD-HHMMSS>/``）：
  raw.jsonl   每个 (query, provider) 一行：耗时、错误、完整结果（含 snippet）
  matrix.md   每个 query 一张表，各源 top5 并排，肉眼判断谁答对
  stats.md    成功率 / 延迟 / 空结果率、URL 两两重合度、独占率、域名分布

用法（容器内跑，环境变量已就位）：
  docker run --rm --network host --env-file .env -v "$PWD:/app" -w /app \
    jim-search:latest python scripts/eval_providers.py --top 8

先看会跑哪些源、花多少钱：加 ``--dry-run``（零成本，不发请求）。
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, service  # noqa: E402
from src.providers import normalize_url  # noqa: E402

DEFAULT_PROVIDERS = ("brave", "tavily", "parallel", "baidu", "deepseek", "duckduckgo")
MATRIX_TOP = 5


def _load_queries(path: str) -> List[str]:
    queries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(line)
    return queries


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "?").lower()
    except ValueError:
        return "?"


def _run_one(provider: str, fn, query: str, top: int) -> dict:
    started = time.monotonic()
    try:
        results = fn(query, count=top)
        error = None
    except Exception as exc:  # provider 的错误在这里只记录，不中断评测
        results = []
        error = f"{type(exc).__name__}: {exc}"
    ms = int((time.monotonic() - started) * 1000)
    return {
        "query": query,
        "provider": provider,
        "ms": ms,
        "error": error,
        "results": [
            {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
        ],
    }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _md_cell(items: List[dict], limit: int = MATRIX_TOP) -> str:
    if not items:
        return "—"
    rows = []
    for i, item in enumerate(items[:limit], 1):
        title = (item["title"] or "(no title)").replace("|", "\\|")
        url = item["url"].replace("|", "\\|")
        rows.append(f"{i}. [{title}]({url})")
    return "<br>".join(rows)


def write_matrix(path: str, runs: List[dict], queries: List[str], providers: List[str]) -> None:
    by_key = {(r["query"], r["provider"]): r for r in runs}
    lines = [
        "# 多源搜索结果并排",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}　"
        f"query 数：{len(queries)}　源：{', '.join(providers)}",
        "",
        "每个源最多显示 5 条；完整结果（含 snippet）见 `raw.jsonl`。",
        "",
    ]
    for idx, query in enumerate(queries, 1):
        lines += [f"## {idx}. {query}", "", "| 源 | 用时 | 条数 | 结果 |", "|---|---|---|---|"]
        for provider in providers:
            run = by_key.get((query, provider))
            if not run:
                lines.append(f"| {provider} | - | - | 未运行 |")
                continue
            status = run["error"] or str(len(run["results"]))
            lines.append(
                f"| {provider} | {run['ms']}ms | {status} | {_md_cell(run['results'])} |"
            )
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_stats(path: str, runs: List[dict], queries: List[str], providers: List[str]) -> None:
    lines = [
        "# 多源搜索统计",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 汇总",
        "",
        "| 源 | 有结果 | 空结果 | 报错 | 平均耗时 | 平均结果数 |",
        "|---|---|---|---|---|---|",
    ]
    for provider in providers:
        rows = [r for r in runs if r["provider"] == provider]
        if not rows:
            continue
        ok = sum(1 for r in rows if r["results"])
        empty = sum(1 for r in rows if not r["results"] and not r["error"])
        errors = sum(1 for r in rows if r["error"])
        avg_ms = int(sum(r["ms"] for r in rows) / len(rows))
        avg_n = round(sum(len(r["results"]) for r in rows) / len(rows), 1)
        lines.append(f"| {provider} | {ok} | {empty} | {errors} | {avg_ms}ms | {avg_n} |")

    # 逐 query 的 URL 集合（按源汇总）
    sets: Dict[str, set] = {p: set() for p in providers}
    for run in runs:
        if run["provider"] in sets:
            sets[run["provider"]].update(
                normalize_url(item["url"]) for item in run["results"] if item["url"]
            )

    lines += [
        "",
        "## URL 重合度（Jaccard，全部 query 合并）",
        "",
        "数值越低越说明两家索引互补；1.0 表示完全重合。",
        "",
        "| |" + "|".join(providers) + "|",
        "|---|" + "|".join("---" for _ in providers) + "|",
    ]
    for a in providers:
        cells = []
        for b in providers:
            cells.append(f"{_jaccard(sets[a], sets[b]):.2f}" if a != b else "-")
        lines.append(f"| **{a}** |" + "|".join(cells) + "|")

    # 独占率：只被一个源返回的 URL（跨 query 合并）
    counts: Counter = Counter()
    for provider in providers:
        counts.update(sets[provider])
    lines += [
        "",
        "## 独占率",
        "",
        "只被单一源收录、别家都没有的 URL 占比，越高越不可替代。",
        "",
        "| 源 | 结果 URL 数 | 独占 URL 数 | 独占率 |",
        "|---|---|---|---|",
    ]
    for provider in providers:
        total = len(sets[provider])
        unique = sum(1 for url in sets[provider] if counts[url] == 1)
        rate = f"{unique / total:.0%}" if total else "-"
        lines.append(f"| {provider} | {total} | {unique} | {rate} |")

    lines += ["", "## 域名分布（各源 top5）", ""]
    for provider in providers:
        domains: Counter = Counter()
        for run in runs:
            if run["provider"] == provider:
                domains.update(_domain(item["url"]) for item in run["results"] if item["url"])
        top = "、".join(f"{d}({n})" for d, n in domains.most_common(5)) or "—"
        lines.append(f"- **{provider}**：{top}")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queries",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_queries.txt"),
        help="query 列表文件（一行一条，# 开头为注释）",
    )
    parser.add_argument("--top", type=int, default=8, help="每个源取多少条（默认 8）")
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help=f"逗号分隔，默认 {','.join(DEFAULT_PROVIDERS)}",
    )
    parser.add_argument("--out", default="", help="输出目录；默认 eval/<时间戳>")
    parser.add_argument("--workers", type=int, default=0, help="并发数；默认等于源数量")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不发请求")
    args = parser.parse_args()

    queries = _load_queries(args.queries)
    if not queries:
        print(f"没有从 {args.queries} 读到 query", file=sys.stderr)
        return 2

    requested = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    providers: List[str] = []
    skipped: List[str] = []
    for name in requested:
        if name not in service._REGISTRY:
            print(f"未知源 {name!r}，跳过", file=sys.stderr)
            continue
        # 未配 key / 当天熔断的源直接跳过，避免空转
        if not service._is_available(name):
            skipped.append(name)
            continue
        providers.append(name)

    if not providers:
        print("没有可用的源（检查 .env 里的 API key）", file=sys.stderr)
        return 2

    print(f"query {len(queries)} 条 × 源 {len(providers)} 个：{', '.join(providers)}")
    if skipped:
        print(f"跳过（未配置 key 或已熔断）：{', '.join(skipped)}")
    print("预计调用次数：" + str(len(queries) * len(providers)))
    if args.dry_run:
        for i, query in enumerate(queries, 1):
            print(f"{i:2d}. {query}")
        return 0

    out_dir = args.out or os.path.join(
        "eval", datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    os.makedirs(out_dir, exist_ok=True)

    workers = args.workers or len(providers)
    runs: List[dict] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, query in enumerate(queries, 1):
            futures = [
                pool.submit(_run_one, name, service._REGISTRY[name], query, args.top)
                for name in providers
            ]
            batch = [f.result() for f in futures]
            runs.extend(batch)
            summary = ", ".join(
                f"{r['provider']}={'ERR' if r['error'] else len(r['results'])}"
                for r in batch
            )
            print(f"[{i}/{len(queries)}] {query} → {summary}", file=sys.stderr)

    raw_path = os.path.join(out_dir, "raw.jsonl")
    with open(raw_path, "w", encoding="utf-8") as fh:
        for run in runs:
            fh.write(json.dumps(run, ensure_ascii=False) + "\n")

    write_matrix(os.path.join(out_dir, "matrix.md"), runs, queries, providers)
    write_stats(os.path.join(out_dir, "stats.md"), runs, queries, providers)

    elapsed = int(time.monotonic() - started)
    print(f"完成，用时 {elapsed}s，输出目录：{out_dir}")
    for name in ("matrix.md", "stats.md", "raw.jsonl"):
        print(f"  - {os.path.join(out_dir, name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
